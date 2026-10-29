DATA GUIDE
Pack track: experienced. Historical capture: 31 July 2026. Run: 4a4b71d9-9b0d-4497-85f1-7db5c4e21472.

Each .jsonl file contains one complete JSON object per line, encoded as UTF-8. A blank or null field is unknown, not a zero or an observation of absence. The schema file documents record types; you may map them into your own application contracts.

prompts.jsonl: prompt_id, run_id, text. The text is the exact question sent to the provider. It was checked against the provider request, not copied from the earlier prompt-generation table.

responses.jsonl: response_id, prompt_id, run_id, engine, engine_name, model_version, answer_text, answer_markdown, capture_status, provider_timestamp, archive_stored_at, settings, capture_route, provider_api_version and provider_task_id. Keep the IDs unchanged. answer_text preserves full_response from the supplied archive, including any citation section appended there. answer_markdown preserves the separately archived markdown field. They are two views of one response, not two independent observations. The untouched archived provider payload is in provenance/provider_payloads.jsonl for inspection.

citations.jsonl: one returned citation occurrence per row, with a unique citation_id, response_id, prompt_id, engine, order, citation_type, url and available source metadata. A URL can appear inline and in the source panel, or recur in multiple answers. Define and disclose any deduplication you use. URL fragments and tracking parameters are preserved. Titles and publication dates can be missing. A citation is not evidence that you have inspected the page.

settings.force_web_search is provider-request metadata when recorded. null does not imply false. provider_timestamp is the provider's capture timestamp; archive_stored_at is when the response batch was stored. All dates remain strings as supplied.

Engine codes: aimode = Google AI Mode, chatgpt = ChatGPT, gemini = Gemini. Model names are historical provider labels, not a recommendation of which model to use now. A local model or a hosted adapter you are authorised to use can analyse these saved answers; you do not need to query these three engines again.

Production scores, model classifications, old prompt labels and internal storage paths are not candidate inputs. Infer useful categories from the actual questions. The lossless JSON inputs are authoritative; no Excel cell-length limit applies.

reference/brand_snapshot.json contains dated homepage structured data, not verified business outcomes. reference/domain_guidance.jsonl contains brief dated summaries with public source URLs. These materials support orientation and evidence checking. Websites are optional reading; your pack can be read and its supplied input files checked offline.
