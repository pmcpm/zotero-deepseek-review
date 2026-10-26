# Task and evidence contracts

`scripts/workflow.py --help` lists the commands. Each subcommand takes the task directory as its first positional argument. Use the bundled Python runtime. All files belong to the current task; the script has no GUI automation, Zotero writes or API credentials.

## Files and stage transitions

- `init TASK --config supplied.json` creates `task.json`, `evidence.json`, `supplements.json` and `state.json`. Existing supplied answers are retained; `check TASK` reports only missing fields. Ask clarification for those fields, then update task configuration.
- `assign-ids TASK` gives new evidence cards monotonically increasing `R001` IDs. Existing IDs remain stable; duplicate IDs are rejected. Removing an item does not authorize renumbering existing cards.
- `metadata-audit TASK` produces the pre-import audit. It detects missing core metadata, DOI syntax, duplicate DOI/title candidates and missing source-based Codex checks. Resolve candidates against source pages before import.
- `metadata-audit TASK --readback zotero-readback.json` compares current expected metadata with Zotero JSON, preserving author order, article numbers and organization-author structures. It also checks the target collection and any declared attachment parent keys. A successful post-import audit bound to current metadata is required before writing.
- `supplements TASK` checks expected local files and SHA256, records receipt/change, and reports pending work. Receipt never implies evidence verification. After reading the correct full text, Codex marks the matching supplement `fulltext_verified` and updates its evidence card. A changed PDF returns to review. Pending supplements block model writing while other search work can continue.
- `prompt TASK` saves a readable prompt preview and message JSON without calling the model.
- `write TASK` checks current models through the configured adapter (CSTCloud by default), calls the configured model and mechanically audits each response. The confirmed execution policy controls revision limits: `until_pass` has no content-revision count limit; `bounded` permits the initial call plus `max_revisions` revisions. Mechanical failures are returned to the model with their evidence context. A mechanical pass waits for an exact count and Codex semantic review.
- `audit TASK --word-count count.json` performs another read-only mechanical audit. `review TASK --semantic-review review.json --word-count count.json` returns semantic problems to the model or creates `selected.json` after all checks pass. After a revision, repeat semantic review against the new body hash.
- `--mock-responses responses.json` on `write` and `review` accepts an array of strings or OpenAI-shaped responses for isolated tests. A mock selection always has `output_document_allowed=false`.

Each `writing/attempt-NN/` contains immutable `task.json` and `evidence.json` snapshots, `messages.json`, `prompt.txt`, `request.json`, `raw.json`, `body.txt`, `audit-initial.json`, and `manifest.json` including hashes and usage. Later audits/reviews use distinct filenames. Keep raw response files internal; never print model reasoning fields. Model content is saved byte-for-byte as UTF-8. Corrections create a new attempt rather than modifying a previous body. Changes to task configuration or evidence after a model call require a new writing attempt; approving the earlier body against changed inputs is rejected.

`selected.json` records the approved body path and SHA256, task/evidence hashes, count, semantic review path and simulation flag. Starting a new writing pass invalidates the selection pointer and archives the previous selection. The Word stage calls `workflow.load_selected(Path(task_directory))`; it rejects stale or modified selections, mismatched immutable input snapshots, incomplete supplements, invalid post-import collection binding, altered model response files, incomplete review or count checks, and mock output. Only isolated tests may explicitly pass `allow_simulation=True`.

## Task configuration

Required confirmation fields are `title`, `audience`, `word_limit`, `reference_target`, `language_ratio`, `citation_style`, `hide_doi`, `bilingual`, `date_range`, `source_scope`, `supplement_dir`, `output_docx`, and `zotero.collection_name`. Boolean false is a valid supplied answer. Date/source fields may explicitly state no additional restriction. Record the user decision rather than infer it from absent values.

New tasks use `schema_version=2` and also require confirmed `execution_policy.allow_subagents` (a JSON boolean) and `execution_policy.revision_mode` (`until_pass` or `bounded`). Their initial `null` values mean unanswered, not permission. `until_pass` requires `max_revisions=null`; `bounded` requires a non-negative JSON integer, defaulting to 2 when no different bound is supplied. Version 1 tasks with no execution policy retain bounded behavior with the legacy default of two revisions; absence never authorizes delegation. Reuse explicit session authorization and record its source rather than asking again. Any updated policy must be included in the next immutable task snapshot; it cannot retroactively approve a response bound to different inputs.

Other fields:

```json
{
  "schema_version": 2,
  "task_id": "generated UUID",
  "scope": "related_work",
  "context": "The research setting and terminology",
  "word_count_basis": "word",
  "language_ratio": {"zh": 1, "en": 1, "strict": false},
  "date_range": {"start": "2015-01-01", "end": "2026-10-25"},
  "source_scope": "all_journals",
  "zotero": {"library_id": 1, "collection_key": "TASKKEY1", "collection_name": "User-confirmed destination"},
  "outline": [{"title": "1 Theme", "budget": 400, "reference_ids": ["R001"], "argument": "Compare the methods supporting this claim"}],
  "author_materials": [],
  "model": "deepseek-v4-flash",
  "execution_policy": {"allow_subagents": true, "revision_mode": "until_pass"},
  "max_revisions": null
}
```

`citation_style` is a CSL style ID or a canonical style alias resolved by the Word/CSL stage. Ratios are desired proportions, not a universal requirement; Codex evaluates approximate versus strict requirements against selected literature. Default formatting is A4, 2.54 cm margins, 宋体 12 pt, 黑体 headings, 1.5 spacing, two-character first-line indent, all black. A user template overrides those defaults. Model prompt length and reference requirements are derived from the current task, never the original example paper.

The default count basis is Microsoft Word word count, including headings while excluding citation IDs and bibliography. `characters_without_whitespace` is supported when the user explicitly chooses it. Before exact Word counting, mechanical auditing uses a clearly labelled CJK/Latin-token estimate; the estimate alone cannot approve final delivery. The Word helper output must be `{ "count": 1234, "basis": "word", "exact": true, "body_sha256": "SHA256 of exact UTF-8 body.txt bytes" }`. `exact` must be the JSON boolean `true`; `count` must be a positive JSON integer rather than a string or boolean.

## Evidence card

```json
{
  "reference_id": "R001",
  "metadata": {"itemType": "journalArticle", "title": "Verified title", "creators": [{"creatorType": "author", "lastName": "Li", "firstName": "Ming"}], "date": "2024", "publicationTitle": "Verified journal", "volume": "12", "issue": "2", "pages": "101575", "DOI": "10.1234/example", "language": "zh-CN"},
  "sources": [{"url": "https://publisher.example/article", "type": "publisher"}],
  "verification": {"metadata_checked": true, "fulltext_checked": true, "evidence_location": "p.4, section 3, table 2"},
  "research_object": "The actual study population or analysis unit",
  "data": "The source and scale verified in the paper",
  "method": "Verified method",
  "findings": "Verified findings without invented values",
  "supported_claims": ["Claim permitted by this evidence"],
  "required": true,
  "requires_fulltext": true,
  "zotero_key": "ITEMKEY1",
  "snapshotToken": "Opaque bridge snapshot token",
  "attachment_keys": ["ATTACH01"]
}
```

Source-based checking is a Codex responsibility: a true flag records a completed review, not an automated assertion. Core import fields are checked; optional absent issue/page/DOI values are left absent when the source has none. Pass Zotero-style organization creators using their verified `name` representation; preserve the returned field structure during readback review. Full text and evidence locations are required by default. Mark `requires_fulltext=false` only for claims explicitly limited to verified abstract/publisher-page content, with that limitation recorded in the card.

`readback.json` is an array of `{key,data}` local API items or flat Zotero item-data objects. Include child attachment objects when evidence cards declare attachment keys. Keep bridge `snapshotToken` opaque and independent of the bibliographic normalization hash; the bridge uses it to detect concurrent user edits before writes.

## Supplement and semantic review

Each supplement entry records `reference_id`, complete `metadata`, `urls`, `expected_filename` and `status`. A filename is a single local filename within the confirmed supplement directory. File checks populate `sha256`, `path` and `size`. Supported states are `waiting_file`, `invalid_file`, `received_requires_fulltext_review`, `changed_requires_review`, and `fulltext_verified`.

Codex writes a semantic review after checking each claim against the referenced evidence:

```json
{
  "body_sha256": "current immutable model body hash",
  "passed": false,
  "issues": [{"reference_id": "R001", "location": "p.4, section 3", "problem": "This sentence mixes inventor and institution analysis units; retain the paper's inventor-level claim"}]
}
```

A passing semantic review uses `passed=true, issues=[]`; `passed` must be a JSON boolean, with its hash matching the immutable body. Source checks, import/readback checks, mechanical audit, exact count and semantic approval are distinct gates. Only a confirmed bounded revision budget stops work because of content-revision count. An authorized `until_pass` task continues without asking again at a fixed count. Missing evidence, service failures, conflicting requirements and explicit user budgets remain separate constraints; preserve the current attempts and report the actual blocker rather than labelling it a revision limit.

## Model transport

`model` is the exact model ID. Optional `model_api` defaults to `{"provider":"cstcloud"}` for both new and legacy tasks. See [model-providers.md](model-providers.md) for generic provider configuration, credential handling and discovery exceptions. Changing provider/model invalidates the approved task snapshot and requires regeneration.
