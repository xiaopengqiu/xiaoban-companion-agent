CREATE TABLE `care_messages` (
	`id` text PRIMARY KEY NOT NULL,
	`thread_id` text NOT NULL,
	`user_id` text NOT NULL,
	`request_id` text,
	`role` text NOT NULL,
	`created_at` text NOT NULL,
	`data` text NOT NULL,
	FOREIGN KEY (`thread_id`) REFERENCES `care_threads`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `care_messages_request` ON `care_messages` (`user_id`,`thread_id`,`request_id`,`role`);--> statement-breakpoint
CREATE INDEX `care_messages_thread` ON `care_messages` (`user_id`,`thread_id`);--> statement-breakpoint
CREATE TABLE `care_preferences` (
	`user_id` text PRIMARY KEY NOT NULL,
	`enabled` integer DEFAULT 0 NOT NULL,
	`revision` integer DEFAULT 0 NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `care_state` (
	`user_id` text PRIMARY KEY NOT NULL,
	`data` text NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `care_threads` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`topic_key` text NOT NULL,
	`evidence_key` text NOT NULL,
	`created_at` text NOT NULL,
	`data` text NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `care_threads_user_time` ON `care_threads` (`user_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `native_push_devices` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`registration_id` text NOT NULL,
	`config_revision` integer NOT NULL,
	`data` text NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `native_push_devices_registration_id_unique` ON `native_push_devices` (`registration_id`);