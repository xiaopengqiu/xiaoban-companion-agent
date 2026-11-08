import {sqliteTable,text,integer,uniqueIndex,index,primaryKey} from 'drizzle-orm/sqlite-core';

export const users=sqliteTable('users',{
  id:text('id').primaryKey(),email:text('email').notNull().unique(),password_hash:text('password_hash').notNull(),
  salt:text('salt').notNull(),data:text('data').notNull(),created_at:text('created_at').notNull(),
});
const owner=()=>text('user_id').notNull().references(()=>users.id,{onDelete:'cascade'});
export const sessions=sqliteTable('sessions',{token_hash:text('token_hash').primaryKey(),user_id:owner(),expires_at:text('expires_at').notNull()});
export const preferences=sqliteTable('preferences',{user_id:owner().primaryKey(),data:text('data').notNull(),revision:integer('revision').notNull().default(0)});
export const events=sqliteTable('events',{id:text('id').primaryKey(),user_id:owner(),source:text('source').notNull(),external_id:text('external_id'),data:text('data').notNull()},t=>[
  uniqueIndex('events_source_key').on(t.user_id,t.source,t.external_id),index('events_user').on(t.user_id)]);
export const eventSuppressions=sqliteTable('event_suppressions',{user_id:owner(),source:text('source').notNull(),external_id:text('external_id').notNull(),created_at:text('created_at').notNull()},t=>[primaryKey({columns:[t.user_id,t.source,t.external_id]})]);
export const memories=sqliteTable('memories',{id:text('id').primaryKey(),user_id:owner(),data:text('data').notNull()},t=>[index('memories_user').on(t.user_id)]);
export const chat=sqliteTable('chat',{id:text('id').primaryKey(),user_id:owner(),data:text('data').notNull()},t=>[index('chat_user').on(t.user_id)]);
export const reminders=sqliteTable('reminders',{id:text('id').primaryKey(),user_id:owner(),dedupe_key:text('dedupe_key').notNull(),data:text('data').notNull()},t=>[uniqueIndex('reminders_dedupe').on(t.user_id,t.dedupe_key),index('reminders_user').on(t.user_id)]);
export const devices=sqliteTable('devices',{id:text('id').primaryKey(),user_id:owner(),token:text('token').notNull().unique(),data:text('data').notNull()});
export const integrations=sqliteTable('integrations',{user_id:owner().primaryKey(),data:text('data').notNull()});
export const leases=sqliteTable('leases',{lease_key:text('lease_key').primaryKey(),user_id:owner(),owner:text('owner').notNull(),expires_at:integer('expires_at').notNull()});
export const limits=sqliteTable('rate_limits',{bucket:text('bucket').primaryKey(),hits:integer('hits').notNull(),reset_at:integer('reset_at').notNull()});
export const serviceState=sqliteTable('service_state',{state_key:text('state_key').primaryKey(),value:text('value').notNull()});
export const webhookReceipts=sqliteTable('webhook_receipts',{message_id:text('message_id').primaryKey(),user_id:owner(),state:text('state').notNull(),updated_at:integer('updated_at').notNull()});
export const threads=sqliteTable('threads',{id:text('id').primaryKey(),user_id:owner(),data:text('data').notNull()},t=>[index('threads_user').on(t.user_id)]);
export const carePreferences=sqliteTable('care_preferences',{user_id:owner().primaryKey(),enabled:integer('enabled').notNull().default(0),revision:integer('revision').notNull().default(0)});
export const careState=sqliteTable('care_state',{user_id:owner().primaryKey(),data:text('data').notNull()});
export const careThreads=sqliteTable('care_threads',{id:text('id').primaryKey(),user_id:owner(),topic_key:text('topic_key').notNull(),evidence_key:text('evidence_key').notNull(),created_at:text('created_at').notNull(),data:text('data').notNull()},t=>[index('care_threads_user_time').on(t.user_id,t.created_at)]);
export const careMessages=sqliteTable('care_messages',{id:text('id').primaryKey(),thread_id:text('thread_id').notNull().references(()=>careThreads.id,{onDelete:'cascade'}),user_id:owner(),request_id:text('request_id'),role:text('role').notNull(),created_at:text('created_at').notNull(),data:text('data').notNull()},t=>[uniqueIndex('care_messages_request').on(t.user_id,t.thread_id,t.request_id,t.role),index('care_messages_thread').on(t.user_id,t.thread_id)]);
export const nativePushDevices=sqliteTable('native_push_devices',{id:text('id').primaryKey(),user_id:owner(),registration_id:text('registration_id').notNull().unique(),config_revision:integer('config_revision').notNull(),data:text('data').notNull()});
export const webPushSubscriptions=sqliteTable('web_push_subscriptions',{id:text('id').primaryKey(),user_id:owner(),endpoint:text('endpoint').notNull().unique(),data:text('data').notNull(),created_at:text('created_at').notNull()},t=>[index('web_push_subscriptions_user').on(t.user_id)]);
