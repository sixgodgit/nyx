# Technical Findings

## Token173 API Key Status
- **Display**: config.yaml shows `sk-USh...R4tB` (masked/truncated appearance)
- **Reality**: The actual key is 51 characters, fully functional
- **Location**: `/root/thalamus/keys.json` under `中转` and `中转海外`
- **Endpoint**: `https://token173.com/v1/chat/completions`
- **Available models**: gpt-4o-mini (supports 识图/vision), gpt-3.5-turbo, deepseek-v4-flash, etc.
- **Total models**: 141+ vision-capable models available

## Vision Model Configuration
- **Correct config**: `provider: custom:thalamus`, `model: gpt-4o-mini`
- **Why**: Thalamus routes vision requests to gpt-4o-mini which supports image recognition
- **Previous broken config**: `provider: xiaomi`, `model: mimo-v2-omni` (bypassed thalamus)
- **Fix applied**: 2026-07-24, changed in `/root/.hermes/config.yaml`

## Thalamus Routing for Vision
- Vision-related keywords (图片|截图|照片|图像|看图|OCR|识别图|vision|视觉|多媒体) route to gpt-4o-mini
- gpt-4o-mini is available through token173.com (中转海外 provider)
- Thalamus fallback: if vision model fails, falls back to deepseek-v4-flash (no vision)

## MediaMarkt Order Pattern
- Order confirmation emails come from MediaMarkt with subjects like "Bestelling geplaatst"
- Shipping confirmation: "Jouw bestelling is onderweg 🎉"
- Invoice: "Jouw factuur"
- Delivery info is in HTML format (not text/plain) - need to parse HTML
- PostNL tracking is provided for Dutch deliveries
