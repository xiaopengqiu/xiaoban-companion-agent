"""Confirmed personal context and requested life follow-ups, independent of health inference."""
from __future__ import annotations

from datetime import timedelta
import re

from .policy import timestamp


def active_memory(memory, now):
    return not memory.get('expires_at') or timestamp(memory['expires_at']) > now


def memory_view(memory, now):
    return {**memory, 'expires_at': memory.get('expires_at'), 'is_active': active_memory(memory, now)}


def history_after_expiry(history, memories, now):
    # Removing an expired fact from retrieval alone is insufficient: an old
    # user/assistant turn may still repeat it. Do not replay that old context.
    expired=[timestamp(m['expires_at']) for m in memories if m.get('expires_at') and not active_memory(m,now)]
    if not expired:
        return history
    cutoff=max(expired)
    return [m for m in history if m.get('created_at') and timestamp(m['created_at'])>cutoff]


def category_enabled(category, prefs):
    return prefs.get('life_followups_enabled',True) if category=='life' else category in prefs['reminder_categories']


def thread_values(changes, now, old=None):
    value={'details':'','status':'active','event_at':None,'follow_up_at':None,
           'follow_up_enabled':False,'outcome':''}
    if old:
        value.update(old)
    value.update(changes)
    for key in ('title','details','status','follow_up_enabled','outcome'):
        if value.get(key) is None:
            raise ValueError('事情字段不能留空：'+key)
    newly_enabled=value['follow_up_enabled'] and (not old or not old['follow_up_enabled'])
    if value['follow_up_enabled'] and (newly_enabled or 'follow_up_at' in changes):
        if 'follow_up_at' not in changes or not value.get('follow_up_at'):
            raise ValueError('开启跟进需要明确选择跟进时间')
        when=timestamp(value['follow_up_at'])
        if not now-timedelta(minutes=5)<=when<=now+timedelta(days=180):
            raise ValueError('跟进时间须在当前时间至未来180天内')
    return value


def candidates(threads, now):
    result=[]
    for item in threads:
        if item['status']!='active' or not item.get('follow_up_enabled') or not item.get('follow_up_at'):
            continue
        due=timestamp(item['follow_up_at'])
        if not due<=now<due+timedelta(hours=24):
            continue
        result.append({'category':'life','title':'接着聊聊你在意的事',
            'body':'你之前希望在这个时间接着聊“'+item['title']+'”。现在想聊聊吗？也可以稍后或暂停跟进。',
            'evidence_ids':[item['id']], 'thread_revision':item['revision'],
            'dedupe_key':f"life:{item['id']}:{item['revision']}",
            'expires_at':(due+timedelta(hours=24)).isoformat()})
    return sorted(result,key=lambda c:c['expires_at'])


def suggestions(message, now):
    text=message.strip()
    if refuses_topic(text):
        return []
    if not 12<=len(text)<=2000 or not re.match(r'^(?:(?:其实|说实话|最近|这周|下周|今天|昨天|这几天|本周|这段时间|现在|目前)[，,\s]*)?我',text):
        return []
    if re.match(r'^(?:请)?(?:帮我)?记住',text) or any(x in text for x in ['?','？','例如','比如','假设','假如','如果','举个例子','朋友说','我朋友','我的朋友','帮他','帮她','不想记','不要记','别记','不想保存','不要保存','不会','不打算','没打算','不准备','不想准备','不担心','没有计划']):
        return []
    if re.search(r'(?:别|不要|不想).{0,12}(?:记忆|保存|记住|记录)|(?:没有|没|不再|并无).{0,8}(?:面试|备考|项目|准备|计划)',text):
        return []
    if not any(x in text for x in ['最近','这周','面试','备考','项目','准备','担心']):
        return []
    return [{'type':'memory','text':text,'category':'context','expires_at':(now+timedelta(days=7)).isoformat()},
            {'type':'thread','title':text[:120],'details':text,'follow_up_enabled':False}]


def refuses_topic(message):
    return any(term in message for term in ['不想提','不想聊','别提','不要提','不聊这','先不说','暂时不想说'])


def relevant_context(message, memories, threads):
    if refuses_topic(message):
        return [],[]
    words=re.findall(r'[\u4e00-\u9fff]+',message)
    tokens={word[i:i+2] for word in words for i in range(len(word)-1)}
    tokens.update(re.findall(r'[A-Za-z0-9]{3,}',message.lower()))
    tokens-=set(['我的','我想','我最','我在','我也','我有','我就','我是','我会','最近','今天','现在','这周','一下','一些','什么','怎么','想聊','聊聊','说说','感觉','有点','有些','可以','希望','准备'])
    contexts=[m for m in memories if m['origin']=='user' and m['category']=='context']
    selected=[m for m in contexts if any(token in m['text'].lower() for token in tokens)]
    important=[t for t in threads if any(token in t['title'].lower() or token in t.get('details','').lower() for token in tokens)]
    if selected or important:
        return selected[-1:],important[-1:]
    if any(term in message for term in ['最近的事','最近在忙','最近关注','我的近况','聊聊近况','接着聊','继续聊','之前那件','在意的事','正在发生的事']):
        return contexts[-1:],threads[-1:]
    return [],[]


def personal_reply(style, memories, threads, message=''):
    if refuses_topic(message):
        return '好，我们先不聊这件事。你想换个轻松的话题，还是让我陪你听一会儿？'
    memories,threads=relevant_context(message,memories,threads)
    context=next((m['text'] for m in reversed(memories) if m['origin']=='user' and m['category']=='context'),None)
    important=threads[-1]['title'] if threads else None
    if style=='listen':
        return '我在听。你可以按自己的节奏说，不急着找解决办法。今天最想让我听见的是哪一部分？'
    if style=='plan':
        return (f'关于“{important}”，' if important else '')+'我们可以先选一件你现在想推进的事，拆成一个小步骤。你希望先完成什么？确认后再决定是否保存或安排跟进。'
    note=f'你之前确认的近期处境是“{context[:180]}”。' if context else ''
    note+=f'你还在关注“{important}”。' if important else ''
    return note+'我们可以一起理一理：现在发生了什么，你最在意哪一部分？我会先听你的想法。'
