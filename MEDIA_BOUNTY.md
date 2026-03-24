# NeverHuman Media Bounty List

This document acts as a tracking punch-list for the high-priority visual assets required to solidify NeverHuman's position as a premium, tier-1 open-source repository.

The repository structure, prose, tone, and documentation have been meticulously aligned with a flagship developer product standard. The singular gap is **Media Fidelity**.

Please capture and replace the following placeholder assets using real execution environments on an Apple Silicon device.

## 1. Hero Demonstration GIF
**Target Path:** `assets/hero-demo-placeholder.gif` (Rename the file in README once captured)

**Requirements:**
- A dark-UI framed screen recording (under 15 seconds) showcasing GARY responding locally to a live voice prompt.
- Crucially, show the "Thinking / Reflection" state so developers understand this isn't just ASR -> LLM -> TTS. It's an agent resolving data.
- Recommended tools: CleanShot X for smooth, un-cluttered screen recording.

## 2. Conversation Interface Screenshot
**Target Path:** `assets/chat-ui-placeholder.png`

**Requirements:**
- High-resolution Retina screenshot of the main chat or interaction flow.
- Ensure the MacOS top bar is either cropped or beautiful.
- No dummy data — execute a real, highly technical conversation about a coding problem or system architecture to prove GARY's analytical depth.

## 3. Memory & Inspection Screenshot
**Target Path:** `assets/memory-ui-placeholder.png`

**Requirements:**
- High-resolution Retina screenshot of the Memory spine UI (showing the context vectors, relationship graphs, or Postgres inspection elements).
- Demonstrates the differentiator: GARY is not stateless.

## 4. Benchmark Chart (Critical Addition)
**Target Path:** `assets/benchmark-chart.png` (You will need to manually inject this image into `docs/benchmarks.md` and the README)

**Requirements:**
- A clean, dark-theme bar chart visualizing the incredibly low-latency of the SSD-to-Metal inference engine.
- X-axis: M1 Max, M2 Pro, M3 Max.
- Y-axis: Time in milliseconds.
- Visualizing both Voice Response (TTS) and First Token (LLM) constraints.

## 5. Social Open Graph Preview Card
**Target Path:** `assets/social-preview.png` (Upload to GitHub Repo Settings -> Social preview)

**Requirements:**
- Dimensions: 1280×640px.
- Minimalist text: "NeverHuman: Private local cognitive assistant for Apple Silicon."
- Subtle glow or visual representation of the Mind Daemon.
