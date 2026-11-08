from datetime import datetime, timedelta, timezone
import importlib
import pytest

@pytest.fixture
def policy():
    try:
        return importlib.import_module('companion.policy')
    except ImportError:
        pytest.fail('Companion reminder policy has not been implemented')

def test_quiet_hours_cross_midnight_and_timezone(policy):
    prefs={'quiet_start':'22:00','quiet_end':'08:00','timezone':'Asia/Shanghai'}
    assert policy.is_quiet(datetime(2026,11,7,15,tzinfo=timezone.utc),prefs)
    assert policy.is_quiet(datetime(2026,11,8,0,tzinfo=timezone.utc)-timedelta(seconds=1),prefs)
    assert not policy.is_quiet(datetime(2026,11,8,0,tzinfo=timezone.utc),prefs)

def test_same_day_window_and_disabled_quiet_window(policy):
    prefs={'quiet_start':'12:00','quiet_end':'13:00','timezone':'UTC'}
    assert policy.is_quiet(datetime(2026,11,8,12,30,tzinfo=timezone.utc),prefs)
    prefs['quiet_end']='12:00'
    assert not policy.is_quiet(datetime(2026,11,8,12,30,tzinfo=timezone.utc),prefs)

def test_freshness_requires_actual_relevant_recent_observation(policy):
    now=datetime(2026,11,8,6,tzinfo=timezone.utc)
    assert not policy.has_fresh_evidence([],now)
    assert not policy.has_fresh_evidence([{'start_at':(now-timedelta(days=2)).isoformat()}],now)
    assert policy.has_fresh_evidence([{'start_at':(now-timedelta(minutes=5)).isoformat()}],now)
    assert not policy.has_fresh_evidence([{'start_at':(now+timedelta(days=1)).isoformat()}],now)

def test_budget_counts_in_user_local_day(policy):
    now=datetime(2026,11,8,1,tzinfo=timezone.utc)
    existing=[{'created_at':'2026-11-07T17:00:00+00:00','status':'pending'}, {'created_at':'2026-11-07T15:00:00+00:00','status':'done'}]
    assert policy.daily_count(existing,now,'Asia/Shanghai')==1

def test_unknown_or_incomplete_data_does_not_become_inactivity(policy):
    now=datetime(2026,11,8,6,tzinfo=timezone.utc)
    result=policy.candidates([],now,'Asia/Shanghai')
    assert result==[]
    result=policy.candidates([{'id':'x','kind':'meal','value':1,'unit':'entry','start_at':now.isoformat(),'source':'manual','metadata':{}}],now,'Asia/Shanghai')
    assert not any(x['category']=='activity' for x in result)

def test_upstream_daily_summary_uses_its_declared_date(policy):
    now=datetime(2026,11,8,6,tzinfo=timezone.utc)
    events=[{'id':'summary','kind':'steps','value':4500,'unit':'steps','start_at':'2026-11-07T00:00:00+00:00','end_at':'2026-11-08T00:00:00+00:00','source':'open-wearables','metadata':{'granularity':'daily_summary','summary_date':'2026-11-07'}}]
    days=policy.daily_data(events,now,'Asia/Shanghai')
    assert days[-2]['steps']==4500
    assert days[-1]['steps'] is None
    assert days[-2]['complete']['steps'] is True

def test_in_bed_session_duration_does_not_become_measured_sleep(policy):
    now=datetime(2026,11,8,6,tzinfo=timezone.utc)
    events=[{'id':'sleep','kind':'sleep','value':9,'unit':'hours','start_at':'2026-11-07T15:00:00+00:00','end_at':'2026-11-08T00:00:00+00:00','source':'open-wearables','metadata':{'duration_measure':'session_duration'}}]
    assert policy.statistics(events,now,'Asia/Shanghai')['metrics']['sleep_hours'] is None

def test_partial_summary_can_display_today_but_is_not_a_complete_day(policy):
    now=datetime(2026,11,8,6,tzinfo=timezone.utc)
    event={'id':'partial','kind':'steps','value':1500,'unit':'steps','start_at':'2026-11-08T00:00:00+00:00','end_at':None,'source':'open-wearables','metadata':{'granularity':'daily_summary','summary_date':'2026-11-08','is_complete_day':False}}
    days=policy.daily_data([event],now,'Asia/Shanghai')
    assert days[-1]['steps']==1500
    assert days[-1]['complete']['steps'] is False

def test_malformed_historical_summary_does_not_crash_other_users(policy):
    now=datetime(2026,11,8,6,tzinfo=timezone.utc)
    event={'id':'broken','kind':'steps','value':1500,'unit':'steps','start_at':'2026-11-08T00:00:00+00:00','end_at':None,'source':'open-wearables','metadata':{'granularity':'daily_summary','summary_date':'bad'}}
    assert policy.statistics([event],now,'Asia/Shanghai')['metrics']['steps'] is None

def test_native_push_budget_uses_actual_attempt_day(policy):
    now=datetime(2026,11,8,1,tzinfo=timezone.utc)
    reminders=[{'created_at':'2026-11-07T10:00:00+00:00','push_attempts':['2026-11-07T17:00:00+00:00']},
               {'created_at':'2026-11-07T10:00:00+00:00','push_attempts':['2026-11-07T15:00:00+00:00']}]
    assert policy.push_daily_count(reminders,now,'Asia/Shanghai')==1
