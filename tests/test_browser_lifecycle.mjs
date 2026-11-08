import test from 'node:test';
import assert from 'node:assert/strict';
import {SessionGuard, ReminderTracker} from '../web/lifecycle.mjs';

test('a delayed response stays valid across navigation, but never across accounts', () => {
  const session = new SessionGuard('alice');
  const request = session.capture();
  assert.equal(session.isCurrent(request), true);
  session.replace('bob');
  assert.equal(session.isCurrent(request), false);
  session.replace('alice');
  assert.equal(session.isCurrent(request), false);
});

test('the first reminder on an initially empty account is notified once', () => {
  const tracker = new ReminderTracker();
  tracker.reset([]);
  const reminder = {id: 'first', status: 'pending'};
  assert.deepEqual(tracker.update([reminder]), [reminder]);
  assert.deepEqual(tracker.update([reminder]), []);
});

test('initial history and another account never become new notifications', () => {
  const tracker = new ReminderTracker();
  assert.deepEqual(tracker.update([{id:'old',status:'pending'}]), []);
  tracker.reset([{id:'bob-history',status:'pending'}]);
  assert.deepEqual(tracker.update([{id:'bob-history',status:'pending'}]), []);
  assert.deepEqual(tracker.update([{id:'done',status:'done'}]), []);
});
