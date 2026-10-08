# Five-minute demonstration

1. Open http://127.0.0.1:8101 and select **Seeded issues**.
2. Run validation. Expect INVALID_DATE, MISSING_MEMBER_ID and MISSING_BEGIN, each with source evidence.
3. Record a review with a rationale; rerun the same transaction and confirm the review persists.
4. Export findings CSV.
5. Load **Valid enrollment** and **Termination**; both pass the documented subset.
6. Enter “Do not terminate coverage” in the request field; the keyword classifier returns `review`.
