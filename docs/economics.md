# Reference inference cost

CAR records **reference inference cost**, never actual billed cost. The static catalog
was verified against official public sources on 2026-08-11: Gemini Developer API Paid
Tier Standard and OpenAI public API list prices. Unknown usage or model identity is N/A.

Gemini 3.6 Flash uses $1.50 input, $0.15 cached input, and $7.50 output per million
tokens; thinking is output-priced and counted once. GPT-5.6 Sol uses $5.00 input,
$0.50 cached input, and $30.00 output; above 272,000 input tokens its whole request
uses 2x input and 1.5x output reference rates. Codex's ChatGPT-authenticated CLI is
not API billing; any API value is only a comparison reference. No savings are claimed.
