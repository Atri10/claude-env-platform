---
name: api-design
description: >-
  Design HTTP REST and event-driven APIs that are consistent, evolvable, and
  safe to consume. Covers naming, versioning, error contracts, idempotency,
  pagination, authentication boundaries, and the decisions that cause breaking
  changes. Use when designing a new API surface, reviewing an existing one,
  or adding endpoints to a service.
---

# API Design

An API is a **contract with your consumers**. Once published, breaking it has
real costs: coordinated deployments, client-side fixes, incidents. Design
APIs as if you cannot change them — because in practice you often cannot
without breaking someone. The decisions made at design time are far cheaper
than migrations made at runtime.

This skill covers HTTP/REST APIs and event schemas. The same principles apply
to RPC (gRPC, tRPC) and library APIs — adjust the syntax, keep the principles.

---

## Core principles

1. **Design for the consumer, not the implementation.** The shape of your
   internal domain model is irrelevant. What matters is what the consumer needs
   to do their job, expressed in the consumer's terms.
2. **Be consistent, then be correct.** Inconsistency in naming, error format, or
   pagination style is a tax paid on every client integration. Decide once, apply
   everywhere.
3. **Evolve, don't break.** Prefer additive changes (new fields, new endpoints,
   new enum values) over destructive ones (removing fields, renaming, changing
   semantics). Use versioning as a last resort, not a first.
4. **Make invalid states unrepresentable.** A request body that can express
   impossible combinations will eventually produce impossible inputs. Constrain
   the schema so invalid states cannot be constructed.
5. **Fail explicitly and informatively.** Returning 200 OK with `{"error": true}`
   is lying to the client. HTTP status codes carry semantic meaning — use them.

---

## Resource naming (REST)

### URLs identify resources; methods express intent
- **Nouns, not verbs.** `/orders` not `/createOrder` or `/getOrders`.
- **Plural nouns for collections.** `/orders`, `/users`, `/products`.
- **Hierarchical where meaningful.** `/orders/{id}/items` for items that only
  exist in the context of an order. Avoid deep nesting (> 2 levels).
- **Lowercase, hyphen-separated.** `/shipping-addresses` not `/ShippingAddresses`
  or `/shipping_addresses`.
- **IDs in path segments, filters as query params.**
  `/orders/{id}` for a specific order; `/orders?status=pending&after=2024-01-01`
  for filtering.

### HTTP methods — use their semantics
| Method | Semantics | Idempotent? | Safe? |
|--------|-----------|-------------|-------|
| GET | Retrieve | Yes | Yes |
| POST | Create / non-idempotent action | No | No |
| PUT | Replace (full update) | Yes | No |
| PATCH | Partial update | Conditional | No |
| DELETE | Remove | Yes | No |

- **GET must be safe** — no side effects. Never use GET to trigger state changes.
- **PUT replaces the full resource.** Missing fields are set to null/default.
  If clients only send changed fields, use PATCH.
- **POST for non-CRUD actions.** `/orders/{id}/cancel` (POST) is fine when the
  action is not a simple CRUD operation.

---

## Request and response design

### Request bodies
- **Require only what is necessary.** Optional fields have defaults; required
  fields are truly required. Every required field increases integration friction.
- **Use strong types.** A date is a string in ISO 8601 format (`2024-01-15`),
  not a free-text field. An amount is an integer (cents) or a structured
  `{amount, currency}` object, not a floating-point number.
- **Validate at the boundary, explicitly.** Return a 400 with field-level errors
  if the request is malformed. Never let invalid input reach business logic.
- **Avoid boolean parameters.** `?send_email=true` becomes unclear when there are
  3 email types. Use an enum or a named sub-resource.

### Response bodies
- **Consistent envelope.** Decide once: do you wrap everything in
  `{"data": …, "meta": …}` or return the resource directly? Apply consistently.
- **Return the created/updated resource.** A POST that creates an order should
  return the order (with its server-assigned ID and timestamps), not just 201.
  This saves the client an immediate GET.
- **Include only what the consumer needs.** Over-fetching wastes bandwidth and
  exposes internal fields that become accidental API surface.
- **Timestamps in ISO 8601 UTC.** `"created_at": "2024-01-15T10:30:00Z"` — not
  epoch integers, not locale-formatted strings, not without timezone.
- **IDs as strings.** Even if your DB uses integers, return IDs as strings.
  This avoids JavaScript precision loss for large integers and makes format
  changes non-breaking.

---

## Error contracts

A good error response tells the client: what went wrong, why, and what to do.

### HTTP status codes — use them correctly
- `200 OK` — success with a body.
- `201 Created` — resource created; include `Location` header with the new URL.
- `204 No Content` — success with no body (e.g., DELETE).
- `400 Bad Request` — client error: malformed input, validation failure.
- `401 Unauthorized` — not authenticated (misleading name; it means unauthenticated).
- `403 Forbidden` — authenticated but not authorised for this resource/action.
- `404 Not Found` — resource does not exist (or is not visible to this caller).
- `409 Conflict` — the request conflicts with current state (duplicate, stale).
- `422 Unprocessable Entity` — syntactically valid but semantically invalid.
- `429 Too Many Requests` — rate limited; include `Retry-After` header.
- `500 Internal Server Error` — server fault; never expose internals in the body.

**Never return 200 with an error in the body.** This forces clients to parse
every response to detect failure and breaks standard HTTP tooling.

### Error body format — consistent across all endpoints
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "One or more fields are invalid.",
    "details": [
      {
        "field": "email",
        "code": "INVALID_FORMAT",
        "message": "Must be a valid email address."
      }
    ],
    "request_id": "req_abc123"
  }
}
```

- **`code`** — a stable machine-readable string. Clients switch on this, not on
  `message`. Never change a code once published.
- **`message`** — a human-readable explanation. May change between versions.
- **`details`** — field-level errors for validation failures. Empty array if
  not applicable.
- **`request_id`** — for correlating with server logs. Always include it.

---

## Idempotency

An operation is **idempotent** if calling it multiple times produces the same
result as calling it once. Idempotency is essential for safe retries.

- **All reads are idempotent by definition.** GET, HEAD.
- **PUT and DELETE are idempotent by HTTP contract.** Design them to be so:
  a second DELETE of an already-deleted resource should return 404, not 500.
- **POST is not idempotent by default.** For operations that must be safe to
  retry (payments, order creation, emails), support an **idempotency key**:
  the client sends a unique key (UUID) in a header; the server stores the
  result and returns it on duplicate requests.

```http
POST /orders
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
```

If the key is seen again, return the stored response without re-processing.
Store idempotency keys with a TTL (typically 24 hours).

---

## Pagination

For any endpoint that returns a collection, plan for pagination from day one.
Adding it later is a breaking change.

### Cursor-based (preferred for large, frequently-updated collections)
```json
{
  "data": [...],
  "pagination": {
    "next_cursor": "eyJpZCI6MTIzfQ==",
    "has_more": true
  }
}
```
The cursor encodes the position (typically the last ID or timestamp). Stable
under concurrent inserts/deletes. Cannot jump to page N.

### Offset-based (simpler; use for small, stable collections)
```json
{
  "data": [...],
  "pagination": {
    "total": 1042,
    "offset": 0,
    "limit": 20
  }
}
```
Straightforward but unstable under concurrent mutations (items can shift
between pages). Cap the maximum limit (e.g., 100) to prevent abuse.

**Always include a default limit.** An unbounded collection endpoint is a
denial-of-service risk. Default `limit=20`, maximum `limit=100`.

---

## Versioning

**The best version is no version.** Additive, backward-compatible changes
(new optional fields, new endpoints, new enum values) do not require a version
bump. Design for evolution first.

When a breaking change is unavoidable:
- **URL versioning** (`/v1/orders`, `/v2/orders`) — explicit, cacheable, easy
  to route. The most common approach for REST APIs.
- **Header versioning** (`Accept: application/vnd.api+json; version=2`) — cleaner
  URLs but harder to test and document.

**Never remove or rename a field without a version bump.** Adding fields is
safe; removing them is not. Changing the type of a field is a breaking change.

**Run both versions in parallel.** Give clients a migration window (minimum
6 months for external APIs, coordinate for internal APIs). Log usage of the old
version to know when it is safe to retire.

---

## Authentication and authorisation boundaries

- **Authentication at the edge.** Verify identity (JWT, API key, session) in a
  middleware or gateway layer — not in every endpoint handler.
- **Authorisation in the domain.** "Can this user perform this action on this
  resource?" is business logic and belongs in the service layer, not the HTTP
  layer. The handler passes the verified identity; the service enforces rules.
- **Return 401 vs 403 correctly.** 401 means "I don't know who you are". 403
  means "I know who you are, but you cannot do this." Conflating them leaks
  information about resource existence.
- **Don't leak existence through 404 vs 403.** For sensitive resources, return
  404 for both "not found" and "forbidden" to avoid confirming the resource
  exists. For non-sensitive resources, prefer the accurate status.
- **Scope API keys narrowly.** A key for reading orders should not be able to
  create them. Enforce this at the middleware layer.

---

## What makes a breaking change

These changes break existing clients:
- Removing a field from a response.
- Renaming a field (add the new name alongside the old, then deprecate).
- Changing a field's type (string → integer, optional → required).
- Changing the semantics of a field (even if the name and type stay the same).
- Removing an endpoint.
- Changing a URL (add a redirect, not a removal).
- Changing an HTTP method.
- Narrowing what the server accepts (removing valid enum values, tightening
  validation).
- Changing error codes that clients switch on.

These changes are **safe** (additive):
- Adding a new optional field to a request or response.
- Adding a new endpoint.
- Adding a new enum value (warn clients to handle unknown values gracefully).
- Relaxing validation (accepting more than before).
- Adding a new HTTP method to an existing resource.

---

## Review checklist for a new API surface

- [ ] Resources are named as nouns, plural, lowercase-hyphenated.
- [ ] HTTP methods match their semantics (GET is safe, PUT is idempotent, etc.).
- [ ] Request validation is explicit with field-level errors on 400/422.
- [ ] Error responses use the consistent error envelope with stable `code` values.
- [ ] All collection endpoints are paginated with a default and maximum limit.
- [ ] Timestamps are ISO 8601 UTC; IDs are strings.
- [ ] POST operations that must be retry-safe support an idempotency key.
- [ ] Authentication is at the edge; authorisation is in the domain.
- [ ] No breaking changes to existing contracts without a version bump.
- [ ] A regression test exists for each endpoint's happy path and error cases.
