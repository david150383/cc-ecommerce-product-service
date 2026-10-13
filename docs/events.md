# Product Service: messages

**None in v1.** No other service currently needs to react to catalog changes. Order snapshots
prices synchronously at order creation (`/internal/v1/products/price-lookup`).

Candidates for later, when a consumer appears: `ProductPublished`, `ProductPriceChanged`
(e.g. for a search index or price-drop notifications). An event is added when a consumer
needs it, not in advance.
