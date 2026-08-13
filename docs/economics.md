# Reference inference cost

CAR records **reference inference cost**, never actual billed cost. The static catalog
was verified against official public sources on 2026-08-11: Gemini Developer API Paid
Tier Standard and OpenAI public API list prices. Unknown usage or model identity is N/A.

Gemini 3.5 Flash-Lite uses $0.30 input and $2.50 output per million tokens; Google
prices output including thinking tokens, and CAR's normalized output/thinking dimensions
are counted exactly once. This catalog snapshot does not include a verified cached-input
rate for Flash-Lite, so any reported cached input leaves reference cost N/A rather than
being priced by inference. GPT-5.6 Sol uses $5.00 input,
$0.50 cached input, and $30.00 output; above 272,000 input tokens its whole request
uses 2x input and 1.5x output reference rates. Codex's ChatGPT-authenticated CLI is
not API billing; any API value is only a comparison reference. No savings are claimed.
