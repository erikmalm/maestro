# Local model suitability

Assessment date: 2026-10-02. Installed models cover lightweight chat/extraction, reasoning and coding. No new model is needed for the first increment. Keep Qwen as chat default, evaluate gpt-oss for reflection and use Devstral for coding on demand. **Task models & reflection** saves independent choices; blank choices use these tags when discovered installed, otherwise the local chat default. Automatic chat curation uses the memory model (Qwen) then the reflection model (gpt-oss) for independent review. Periodic working-note reflection reverses those roles. Both use structured output and low/off thinking when supported; automatic mode needs at least 8K context. Explicit-context calls select by task kind; autonomous coding and general task dispatch remain planned.

## Installed models and primary evidence

| Installed tag | Quantization | Intended use and evidence |
| --- | --- | --- |
| `qwen2.5:7b` | Q4_K_M | Lightweight chat/extraction. The [Qwen model card](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct) describes improved instruction following and structured JSON; correctness still needs validation. |
| `gpt-oss:20b` | MXFP4 | Candidate for reasoning over corrections/outcomes. [OpenAI's Ollama guide](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-ollama) recommends at least 16 GB VRAM/unified memory for this size. |
| `devstral-small-2:24b` | Q4_K_M | Coding specialist. [Mistral's offline guide](https://docs.mistral.ai/vibe/code/cli/offline-models) identifies the dense 24B agentic/coding model and recommends 24 GB VRAM for 4-bit/32K context; CPU offload is slower. |

All three locally report completion/tools; gpt-oss additionally reports thinking and Devstral vision. Metadata establishes interfaces, not quality. Disk sizes are approximately 4.7/13/15 GB, which are not runtime RAM/VRAM requirements.

## Bounded measurements on this computer

Hardware: RTX 4070 Ti SUPER, 16,376 MiB VRAM, approximately 63.2 GiB system RAM. About 2,672 MiB VRAM was already occupied. Ollama 0.35.0; installed digest prefixes `845dbda0ea48`, `17052f91a42e`, `24277f07f62d`.

Twelve sequential calls used four public extraction fixtures per model: Swedish preference, later correction, abstention from assistant claims/secrets and a quoted injection. Each used JSON-schema output, temperature 0, seed 42, 4,096 context tokens, a 512-token output cap and `keep_alive=0`. gpt-oss used low reasoning. No private chats, personal keys, paid inference or downloads were involved.

| Model | Evidence/quote checks | Median end-to-end time | Median generation tokens/s | Sampled model allocation / VRAM |
| --- | --- | --- | --- | --- |
| Qwen 2.5 7B | 3/4 | 2.41 s | 118.2 | Not captured before unload |
| gpt-oss 20B | 4/4 | 6.02 s | 131.2 | 11.86 / 11.86 GiB |
| Devstral Small 2 24B | 4/4 | 7.14 s | 19.2 | 14.31 / 12.89 GiB |

Qwen abstained in the injected-quote fixture: it avoided the malicious claim but missed the valid preference. gpt-oss retained the correct latest source/fact but quoted the whole correction sentence; this passes evidence checks and fails exact byte matching. Devstral passed all exact matches. Token rates include gpt-oss reasoning and are not comparable answer throughput.

Allocations are sampled Ollama `/api/ps` fields, not total process RAM or guaranteed peaks; missing samples do not mean zero use. Devstral's model allocation exceeded its GPU portion by about 1.43 GiB, consistent with CPU placement. gpt-oss fit on GPU in these short calls. Other applications and larger contexts change placement/speed. Median load times were 2.18/4.74/5.93 seconds; these unloaded-model calls used OS caches, not fresh-boot or warm-residency measurements. All models unloaded at completion.

This is short extraction, not long-history recall, autonomous planning, code editing or verified lesson quality. Four fixtures/one seed cannot establish automatic curation quality. Schema controls shape, not truth. The reflection worker now requests schema output and low/off thinking when supported, following [Ollama's structured-output](https://docs.ollama.com/capabilities/structured-outputs) and [thinking controls](https://docs.ollama.com/capabilities/thinking). Automatic mode permits normalized facts with exact source evidence and independent review; both modes still need broader quality evaluation.

Reproduce deliberately with other Ollama sessions idle:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_memory_models.py --output "$env:LOCALAPPDATA\Maestro\benchmarks\memory-probe.json"
```

The raw report stays outside the checkout. The opt-in probe reads only Ollama metadata and public fixtures. It does not run in CI or at startup. An HTTP timeout may leave inference running until the server finishes; `keep_alive=0` then unloads, and the report records remaining loaded models.

## Automatic workflow smoke (2026-10-03)

A separate temporary database and one synthetic preference exercised the real two-stage curation path at 8,192 context tokens, a 512-token per-call output cap and the default 10,000-token daily allowance. Qwen formed a concise preference with exact user evidence; gpt-oss independently approved it. Both calls settled, a curated memory and journal entry persisted, and the workflow took 8.83 seconds with 580 actual input/output tokens. A current-version periodic probe preserved an existing synthetic preference and added an independently reviewed lesson with the model order reversed (9.79 seconds, 850 actual tokens). Both workflows left no loaded models in Ollama afterward.

### Expanded background context (2026-10-03)

The background context is now configurable independently of chat, defaulting to 32,768 tokens. A synthetic GPT-oss request with that context and a 128-token output allowance completed in 12.95 seconds, reported 102 input/98 output tokens, settled its ledger and left no loaded model after `keep_alive=0`. Ollama reported the complete model on the GPU at 12,765,573,938 bytes (11.89 GiB). Peak total GPU use was 15,382 of 16,376 MiB, leaving about 994 MiB free on this desktop. This establishes that one short 32K-context request fits, not that longer prompts or other GPU workloads will have the same latency or headroom. The private temporary database contained only synthetic input.

Keep one generation at a time. Larger contexts allocate more memory and can trigger CPU offloading; the new 131,072-token background maximum is an available setting, not a measured recommendation for this 16 GiB GPU. See [Ollama context guidance](https://docs.ollama.com/context-length). Model downloads are not required for the feedback increment; evaluate reviewed lessons and repeated mistakes with the existing models first.

A first live pass with complete exchanges was rejected before promotion because generated source/evidence fields did not match exact user evidence. A private disposable copy reproduced the validation category without logging chat or generated prose. The periodic schema now requires empty source/evidence fields, with exchange provenance supplied by Maestro. A subsequent pass reached review, where Qwen approved two indices for one draft; review bounds now follow the captured draft count. Factual curation retains exact-user-evidence checks. These cases demonstrate why schema bounds must follow the actual task, and why validation and visible rejection reasons remain necessary alongside independent model review.

After both schema fixes, the deployed six-hour workflow completed at 13:31 local time: gpt-oss formed a working lesson, Qwen independently approved it, and Maestro saved the lesson and journal entry. Both models unloaded; the next pass was scheduled for 19:30. No private prose was copied into this report.

The synthetic smoke and hardware checks used no personal chats; live validation and its disposable copies kept private text local and reported only status and validation categories. These checks establish end-to-end execution only. Repeated Swedish/English correction, scope, abstention and sanitation-quality trials remain MODEL-03/REF-04 work; no additional model or dependency was installed.
## Diversification and next measurements

Use one model at a time and bounded context. Unloaded installed models consume no inference memory; an idle background worker should not keep an LLM resident. Devstral's observed offload/slower throughput make it a poor frequent-reflection default on this GPU. gpt-oss is the stronger first reflection candidate here; Qwen is faster for ordinary interaction. Compare both before choosing automation defaults.

Do not add another large general model now. If keyword recall proves inadequate, [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) is a small multilingual retrieval candidate, not a chat/reflection replacement. Compare embeddings to scoped lexical/FTS recall on paraphrases before adding weights, indexes or dependencies. Nothing was downloaded.

MODEL-03 remains: repeated trials, realistic batches, evidence-backed lessons, warm/switch latency, complete RAM/VRAM sampling and 4K/8K contexts. Hosted search needs at least 8K, so these 4K measurements do not establish its performance. Verify responsiveness and failure/unload accounting alongside quality.
