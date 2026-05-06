# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changes

- **fix(cron): emit load-time warning for non-dict `origin` in jobs.json (#20725)**
  The scheduler's `_resolve_origin` already treats non-dict `origin` values
  (strings, ints, lists from hand-edits or migration scripts) as missing rather
  than crashing with `AttributeError: 'str' object has no attribute 'get'`.
  However, operators had no way to discover the bad field until it appeared in
  the run-time error log after the job fired.

  `cron/jobs.py` now calls `_warn_non_dict_origins()` inside `load_jobs()` and
  emits a `logger.warning` for every job whose `origin` is non-null and
  non-dict.  This surfaces the problem at load time (gateway startup, daemon
  tick) before any job runs.  Null and valid dict `origin` values are
  unaffected.
