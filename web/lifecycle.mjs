export class SessionGuard {
  constructor(token = null) { this.token = token; this.revision = 0; }
  replace(token) { this.token = token; this.revision += 1; }
  capture() { return {token: this.token, revision: this.revision}; }
  isCurrent(ticket) { return this.token === ticket.token && this.revision === ticket.revision; }
}

export class ReminderTracker {
  constructor() { this.initialized = false; this.seen = new Set(); }
  reset(rows) { this.seen = new Set(rows.map(row => row.id)); this.initialized = true; }
  update(rows) {
    const added = this.initialized ? rows.filter(row => !this.seen.has(row.id) && ['pending', 'sent'].includes(row.status)) : [];
    this.reset(rows);
    return added;
  }
}
