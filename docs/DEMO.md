# Demo flow

1. Configure SCOPUS_API_KEY in backend/.env.
2. Start the application.
3. Search a name and optional affiliation to review Scopus Search publication records. Record creators are not verified author identities.
4. Open VFSTR Authors to browse the official CSE roster; use Refresh VFSTR Authors to update it from that same source.
5. Use Import April 2026 metrics to import only exact normalized or exact-token-order matches from the supplied metrics sheet; review the persisted matched/unmatched/ambiguous report.
6. Open a faculty profile and review the Google Scholar and Scopus values marked as an April 2026 snapshot, along with its Updated / Not Updated status and source links.
7. Keep unmatched or ambiguous spreadsheet rows out of profiles until their official identity mapping is confirmed.
8. Configure a linked Google Scholar provider if available for future live synchronization.
9. Click Sync now only for a profile with a verified Scopus Author ID or configured Scholar profile.
10. Review metric history and Search History without describing the April 2026 snapshot as live data.
