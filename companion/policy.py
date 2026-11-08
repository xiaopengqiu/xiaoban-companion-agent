"""Deterministic statistics and respectful reminder gates; no clinical decisions."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo


def timestamp(value):
    if isinstance(value,datetime):
        return value
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def event_time(event,timezone_name=None):
    meta=event.get('metadata',{})
    if meta.get('granularity')=='daily_summary' and meta.get('summary_date'):
        try:
            day=date.fromisoformat(meta['summary_date'])
            zone=ZoneInfo(timezone_name) if timezone_name else timestamp(event['start_at']).tzinfo
            return datetime.combine(day,datetime.min.time(),zone)+timedelta(days=1)-timedelta(microseconds=1)
        except (ValueError,TypeError):
            pass
    return timestamp(event.get('end_at') or event['start_at'])


def is_quiet(now, preferences):
    local = now.astimezone(ZoneInfo(preferences['timezone'])).strftime('%H:%M')
    start,end=preferences['quiet_start'],preferences['quiet_end']
    if start==end:
        return False
    return start<=local<end if start<end else local>=start or local<end


def has_fresh_evidence(events,now,max_hours=24,timezone_name=None):
    return any(timedelta(0)<=now-event_time(e,timezone_name)<=timedelta(hours=max_hours) for e in events)


def daily_count(reminders,now,timezone_name):
    zone=ZoneInfo(timezone_name)
    day=now.astimezone(zone).date()
    return sum((r.get('budget_date') or timestamp(r['created_at']).astimezone(zone).date().isoformat())==day.isoformat() and r['status']!='cancelled' for r in reminders)


def push_daily_count(reminders,now,timezone_name):
    zone=ZoneInfo(timezone_name)
    day=now.astimezone(zone).date()
    return sum(timestamp(attempt).astimezone(zone).date()==day for r in reminders for attempt in r.get('push_attempts',[]))


def value_in(event,kind):
    value=float(event['value'])
    unit=event.get('unit','').lower()
    if kind=='sleep':
        return value/3600 if unit in {'s','second','seconds'} else value/60 if unit in {'min','minute','minutes'} else value
    if kind=='workout':
        return value/60 if unit in {'s','second','seconds'} else value*60 if unit in {'h','hour','hours'} else value
    if kind=='water':
        return value*1000 if unit in {'l','liter','litre'} else value
    return value


def daily_data(events,now,timezone_name,days=21):
    zone=ZoneInfo(timezone_name)
    today=now.astimezone(zone).date()
    grouped=defaultdict(lambda:defaultdict(list))
    for event in events:
        meta=event.get('metadata',{})
        if event['kind']=='sleep' and meta.get('duration_measure')=='session_duration':
            continue
        when=event_time(event,timezone_name)
        try:
            day=date.fromisoformat(meta['summary_date']) if meta.get('granularity')=='daily_summary' and meta.get('summary_date') else when.astimezone(zone).date()
        except (ValueError,TypeError):
            continue
        if day>today or (meta.get('granularity')!='daily_summary' and when>now):
            continue
        if 0<=(today-day).days<days:
            grouped[(day.isoformat(),event['kind'])][event['source']].append(event)
    result=[]
    for offset in reversed(range(days)):
        day=(today-timedelta(days=offset)).isoformat()
        item={'date':day,'steps':None,'sleep_hours':None,'workout_minutes':None,'water_ml':None,'evidence':{},'complete':{}}
        for kind,field in [('steps','steps'),('sleep','sleep_hours'),('workout','workout_minutes'),('water','water_ml')]:
            sources=grouped.get((day,kind),{})
            if not sources:
                continue
            # One source per day prevents double counting mirrored phone/watch streams.
            # Explicit source priority is stable; records document the selected provenance.
            source=max(sorted(sources),key=lambda s:(any(e.get('metadata',{}).get('is_complete_day') for e in sources[s]),len(sources[s])))
            selected=sources[source]
            cumulative=[e for e in selected if e.get('metadata',{}).get('aggregation')=='cumulative']
            values=[value_in(e,kind) for e in selected]
            item[field]=round(max(values) if cumulative else sum(values),2)
            item['evidence'][kind]=[e['id'] for e in selected]
            item['complete'][kind]=any(e.get('metadata',{}).get('is_complete_day',e.get('metadata',{}).get('granularity')=='daily_summary' and date.fromisoformat(day)<today) for e in selected)
        result.append(item)
    return result


def statistics(events,now,timezone_name):
    days=daily_data(events,now,timezone_name)
    today=days[-1]
    metrics={key:today[key] for key in ['steps','sleep_hours','workout_minutes','water_ml']}
    insights=[]
    sleep=[d for d in days if d['sleep_hours'] is not None]
    if len(sleep)>=3:
        last=sleep[-3:]
        avg=round(sum(d['sleep_hours'] for d in last)/3,1)
        insights.append({'title':'最近三个睡眠记录日','body':f'{last[0]["date"]} 至 {last[-1]["date"]}，这三个记录日的睡眠总时长平均为 {avg} 小时。这里只描述记录，不作医学判断。',
                         'evidence_ids':[i for d in last for i in d['evidence']['sleep']],'category':'sleep'})
    completed=[d for d in days[:-1] if d['steps'] is not None and d['complete'].get('steps')]
    if len(completed)>=7:
        recent=completed[-7:]
        avg=round(sum(d['steps'] for d in recent)/len(recent))
        insights.append({'title':'过去七个完整记录日','body':f'完整步数记录日平均 {avg} 步；没有记录的日子未按零步计算。',
                         'evidence_ids':[i for d in recent for i in d['evidence']['steps']],'category':'activity'})
    return {'metrics':metrics,'series':[{k:v for k,v in d.items() if k not in {'evidence','complete'}} for d in days],
            'insights':insights,'last_sync_at':max((e.get('updated_at',e['start_at']) for e in events),default=None)}


def candidates(events,now,timezone_name):
    """Require fresh relevant evidence. Missing data never means no exercise or no meal."""
    result=[]
    days=daily_data(events,now,timezone_name)
    by_id={e['id']:e for e in events}
    sleep_days=[d for d in days if d['sleep_hours'] is not None]
    if len(sleep_days)>=3:
        last=sleep_days[-3:]
        ids=[i for d in last for i in d['evidence']['sleep']]
        avg=sum(d['sleep_hours'] for d in last)/3
        if avg<6.5 and has_fresh_evidence([by_id[i] for i in ids],now,timezone_name=timezone_name):
            result.append({'category':'sleep','title':'给今晚留一点休息时间',
                           'body':f'最近三次睡眠记录平均 {avg:.1f} 小时。今晚想提前留出一段放松时间吗？', 'evidence_ids':ids})
    # Only complete observed days can be compared with the user's own earlier complete days.
    complete=[d for d in days if d['steps'] is not None and d['complete'].get('steps')]
    if len(complete)>=8:
        latest=complete[-1]
        baseline=sum(d['steps'] for d in complete[-8:-1])/7
        ids=latest['evidence']['steps']
        if baseline>0 and latest['steps']<baseline*.65 and has_fresh_evidence([by_id[i] for i in ids],now,36,timezone_name):
            result.append({'category':'activity','title':'按自己的节奏动一动',
                           'body':f'最近一个完整记录日为 {latest["steps"]:.0f} 步，之前七个完整记录日平均 {baseline:.0f} 步。今天方便时，可以安排一小段轻松活动。',
                           'evidence_ids':ids+[i for d in complete[-8:-1] for i in d['evidence']['steps']]})
    return result


def candidate_key(candidate,now,timezone_name):
    day=now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    return candidate['category']+':'+day


def evidence_fingerprint(ids):
    return sha256('|'.join(sorted(ids)).encode()).hexdigest()[:16]
