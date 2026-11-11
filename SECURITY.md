# Security policy

## Scope

`cronlint` reads untrusted files — a crontab drop-in can come from anyone who
can write to `/etc/cron.d`, and CI runs it over whole repositories. The
parsers and rules are therefore part of the attack surface: a crafted cron
file must not be able to hang the scanner, allocate without bound, or
execute anything.

What is in scope:

- a cron file that crashes the tool, hangs it, or makes it allocate without bound;
- a file that makes the tool report *fewer* findings than it should (a parsing
  bug that silently skips a task line is a security bug, not a quality issue);
- a shape misdetection that would cause a reader to "fix" a line and change
  which user it runs as or when it fires;
- a finding that is wrong in a way that could make a reader weaken a schedule.

What is not in scope:

- rules that fire on a pattern you consider acceptable — that is a discussion,
  open an issue;
- the absence of a rule you would like (a PR with a sample line is welcome);
- anything that requires running the scheduled command.

## Reporting

Open a private security advisory on GitHub (`Security` → `Advisories` →
`New draft advisory`). Please include the offending cron bytes or a minimal
reproducer — a five-line file is usually enough.

Expect an initial response within a week. This is a single-maintainer
project, not a product with an SLA.

## Supported versions

The latest release. Cron syntax has been stable for four decades; old
releases will be told to upgrade rather than patched.
