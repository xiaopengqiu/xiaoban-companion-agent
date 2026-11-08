"""Administrative HTTP routes. Role is resolved from persisted authenticated user data."""
import hmac
import json
import os

from fastapi import Depends,HTTPException,Request
from .model_runtime import ModelRuntimeError
from .store import ADMIN_PREDICATE


async def json_payload(request):
    try:
        return await request.json()
    except (ValueError,UnicodeDecodeError):
        raise HTTPException(422,'请输入有效 JSON 对象')


def user_summary(user):
    return {'id':user['id'],'name':user.get('name',''),'email':user.get('email',''),
            'is_admin':user.get('is_admin') is True,'is_demo':bool(user.get('is_demo')),
            'created_at':user.get('created_at')}

def install_model_admin(app,store,runtime,authenticated):
    def current_admin(user):
        # Mutations call this again while holding the write transaction so a
        # concurrent role revocation cannot leave a stale authorization usable.
        row=store.connection.execute('SELECT data FROM users WHERE id=?',(user['id'],)).fetchone()
        data=json.loads(row[0]) if row else {}
        if data.get('is_demo') or data.get('is_admin') is not True:
            raise HTTPException(403,'仅管理员可以管理模型服务')
        return data

    def admin(user=Depends(authenticated)):
        with store.lock:
            return current_admin(user)

    @app.post('/api/internal/admin/grant')
    async def grant(request:Request):
        secret=os.getenv('COMPANION_ADMIN_SETUP_TOKEN','');supplied=request.headers.get('authorization','').removeprefix('Bearer ')
        if len(secret)<32 or not hmac.compare_digest(secret.encode('utf-8'),supplied.encode('utf-8')):raise HTTPException(401,'需要服务端初始化授权')
        data=await json_payload(request)
        if not isinstance(data,dict) or set(data)!={'email'} or not isinstance(data['email'],str):raise HTTPException(422,'请指定已注册账号的邮箱')
        with store.transaction():
            if store.connection.execute("SELECT 1 FROM service_state WHERE state_key='admin_bootstrap_used'").fetchone():raise HTTPException(409,'管理员初始化已经完成')
            row=store.connection.execute('SELECT id,data FROM users WHERE email=?',(data['email'].strip().lower(),)).fetchone()
            if not row or json.loads(row['data']).get('is_demo'):raise HTTPException(404,'请先注册一个正式的小伴账号')
            user=json.loads(row['data']);user['is_admin']=True
            store.connection.execute('UPDATE users SET data=? WHERE id=?',(json.dumps(user,ensure_ascii=False),row['id']))
            store.connection.execute("INSERT INTO service_state VALUES('admin_bootstrap_used','true')")
        return {'granted':True}

    @app.get('/api/admin/model-config')
    def get_config(user=Depends(admin)):
        try:return runtime.public_config()
        except ModelRuntimeError as error:raise HTTPException(503,str(error))

    @app.put('/api/admin/model-config')
    async def save_config(request:Request,user=Depends(admin)):
        data=await json_payload(request)
        try:
            with store.lock:
                current_admin(user)
                return runtime.save(data)
        except ModelRuntimeError as error:raise HTTPException(422,str(error))

    @app.get('/api/admin/users')
    def users(email:str|None=None,user=Depends(admin)):
        if email is not None:
            email=email.strip().lower()
            if not 1<=len(email)<=200:
                raise HTTPException(422,'请输入有效的邮箱筛选条件')
        with store.lock:
            current_admin(user)
            where=' WHERE email=?' if email is not None else ''
            params=(email,) if email is not None else ()
            rows=store.connection.execute("SELECT data FROM users"+where+" ORDER BY json_extract(data,'$.created_at') DESC,id DESC LIMIT 100",params).fetchall()
        return {'items':[user_summary(json.loads(row[0])) for row in rows]}

    @app.post('/api/admin/users/{user_id}/role')
    async def role(user_id:str,request:Request,user=Depends(admin)):
        data=await json_payload(request)
        if not isinstance(data,dict) or set(data)!={'is_admin'} or type(data['is_admin']) is not bool:
            raise HTTPException(422,'管理员开关必须是布尔值，且不能包含额外字段')
        with store.transaction():
            current_admin(user)
            row=store.connection.execute('SELECT data FROM users WHERE id=?',(user_id,)).fetchone()
            if not row:
                raise HTTPException(404,'用户不存在')
            target=json.loads(row[0])
            if target.get('is_demo') and data['is_admin']:
                raise HTTPException(409,'演示账号不能授予管理员权限')
            target['is_admin']=data['is_admin']
            changed=store.connection.execute(f'UPDATE users SET data=? WHERE id=? AND (?=1 OR NOT ({ADMIN_PREDICATE}) OR (SELECT count(*) FROM users WHERE {ADMIN_PREDICATE})>1)',
                (json.dumps(target,ensure_ascii=False),user_id,int(data['is_admin'])))
            if not changed.rowcount:
                raise HTTPException(409,'请至少保留一名管理员')
        return user_summary(target)

    @app.post('/api/admin/model-test')
    def test_config(user=Depends(admin)):
        try:
            runtime.complete([{'role':'user','content':'这是模型连接测试。请只回复：连接成功。'}])
            return {'ok':True,'detail':'模型服务已返回有效回复'}
        except ModelRuntimeError as error:return {'ok':False,'detail':str(error)}
