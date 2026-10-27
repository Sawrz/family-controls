# Telegram and Matrix

The backend owns account languages, child display names, requests and policy changes.
Adapters render the same state and use linked parent identities for every action.

## Language and names

Personal responses use the parent's account language, then the household language,
then English. Change the same preference in **My account**, `/language de` in
Telegram or `!language de` in Matrix. `en`, `de` and `default` are supported.
`default` clears the personal override.

Shared notifications use the configured group/room language, then the household
language. Administrators can change these in **Household administration →
Integrations**, `/grouplanguage de` or `!roomlanguage de`. A personal language
change does not change everyone else's notifications.

Deployment defaults are optional:

```nix
services.family-controls.backend.settings.language = {
  defaultLanguage = "en";
  chatLanguages = { telegram = "de"; matrix = "en"; };
};
```

Once an administrator saves language preferences, the backend persists them across
rebuilds. No messaging credentials are exposed by the preferences API.

Edit a child's display name in **Schedules**. The stable child and computer account
IDs stay unchanged. Without a display name, interfaces capitalize only the first
letter of the child ID. Chat status shows minutes without decimals, the regular and
effective daily windows, and each computer's last contact in the child's timezone.
Allowance and schedule are separate restrictions.

## Everyday controls

Telegram `/family` opens **Add time**, **Extend allowed hours** and **More**.
Submenus replace that message. Select one, several or all children, choose 5, 15,
30 or 60 minutes, or enter a custom duration. The confirmation names every child
and states the minutes each receives. More contains status, schedules, deductions,
blocking, unlimited access, history and language controls.

Matrix `!family` lists the same primary actions; `!more` shows the other commands.
For example:

```text
!addtime lilly 15m
!addtime lilly,lucy 30m
!addtime all 1h 10m
!extendaccess lilly 15m
!status lilly
!history lilly
```

Bulk changes and unlimited-access commands return an explicit `!confirm CODE`
command. Only the parent who prepared it can confirm it, within ten minutes.
Repeating a confirmation cannot grant time twice. If some children cannot be
updated, the reply preserves the confirmation for retry and reports the failures.
Already completed changes are not rolled back or repeated.

Telegram menus and custom replies belong to the parent who opened them. Request
approval buttons may be used by any authorized, linked parent. Telegram still
requires membership as an administrator of the configured parent group.

## Requests and delivery

A request card contains the child's name, requested amount, reason and decision
controls. Telegram offers 5, 15, 30, 60, custom approval and denial. In Matrix,
reply to the request with `!approve 15m` or `!deny reason`; explicit commands with
child and request ID are also shown. Custom amounts use the same duration syntax.

Decisions made in the web, Telegram or Matrix replace the original chat request
message. Telegram removes approval buttons. Expired and cancelled requests also
replace their cards. A stale button or reply cannot approve a newer request.

Receipts show the decision, amount, parent and delivery state. They distinguish
waiting for the computer, applied, failed and expired. Routine receipts do not
repeat budgets, schedules or device details. A failure can later become applied
when the device recovers. An exception expires at the child's local midnight.

Resolved Telegram cards offer **Review remaining requests** when others are
pending, otherwise **Menu**. Resolving a request never posts an unsolicited new
menu. An explicitly opened menu workflow returns to its existing menu.

Adapter state stores message references for seven days and confirmations for ten
minutes. SSE cursors persist across reconnects. Matrix sends and replacements use
stable transaction IDs and the room's configured encryption. Telegram does not
provide a send-message idempotency key: a process failure after Telegram accepts a
new message but before its ID is stored can duplicate that notification; policy
commands remain idempotent.

## Verification boundary

Pytest covers language precedence and authorization, menu ownership, bulk partial
failure/retry, formatting and cross-interface decisions. Browser journeys exercise
language settings and display names against the real API. The central NixOS VM
suite exercises adapters through Unix-socket/TLS backend connections, SSE and real
device enforcement. Telegram and Matrix external transports are simulated in
these tests; they do not send messages to a live household.
