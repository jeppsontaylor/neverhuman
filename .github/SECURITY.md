# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` (latest) | ✅ |
| older commits | ❌ |

## Reporting a vulnerability

**Please do not report security vulnerabilities via public GitHub issues.**

To report a vulnerability:

1. Open a **private** GitHub Security Advisory at:  
   `https://github.com/jeppsontaylor/neverhuman/security/advisories/new`

2. Include:
   - A description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fix (optional)

We will respond within **72 hours** and aim to ship a fix within **14 days** for critical issues.

## Scope

Security issues we care about:
- Unauthorized data access or exfiltration
- Remote code execution via crafted audio or WebSocket messages
- TLS certificate handling bugs
- Postgres injection via pipeline inputs
- Sensitive data written to logs

Out of scope:
- Issues requiring physical access to the Mac
- Browser-level vulnerabilities unrelated to our WebSocket protocol

## Disclosure policy

We follow responsible disclosure. Once a fix is shipped, we will publish a security advisory and credit the reporter (unless they prefer anonymity).
