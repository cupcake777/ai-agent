---
title: Matrix Gateway
sidebar_label: Matrix
---

# Matrix Gateway

Hermes can run a Matrix bot as a messaging gateway. The gateway supports direct
messages and rooms, including rich media, reactions, approvals, model selection,
and optional end-to-end encryption (E2EE) when the Matrix dependencies and
homeserver support it.

## Capability table

| Capability | Status |
| --- | --- |
| text | yes |
| threads | yes |
| reactions | yes |
| approvals | yes |
| model picker | yes |
| thinking panes | yes |
| images | yes |
| multiple images | yes |
| files | yes |
| voice/audio | yes |
| video | yes |
| E2EE | off / optional / required |
| diagnostics | yes |

## Minimal configuration

Add a Matrix platform entry to your Hermes gateway configuration with your
homeserver, bot user ID, and access token. Use a dedicated bot account and invite
it to the rooms where it should respond.

```yaml
gateway:
  platforms:
    matrix:
      enabled: true
      token: "syt_your_access_token"
      extra:
        homeserver: "https://matrix.example.org"
        user_id: "@hermes:example.org"
        device_id: "HERMES"
        encryption: false
```

Set `encryption: true` only after the bot account has a working crypto store and
has joined the encrypted rooms it needs to serve. When encryption is disabled,
Matrix still works for unencrypted rooms and direct messages.

## Notes

- Media messages are downloaded and forwarded to Hermes as attachments when the
  homeserver exposes the content.
- Reactions and approval controls are mapped to gateway actions where the client
  supports them.
- Diagnostics are available through the adapter capability declaration and the
  gateway status surface.
