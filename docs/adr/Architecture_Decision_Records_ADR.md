# Architecture Decision Records (ADR)

> This is a location to record all high-level architecture decisions in the Saude Ja.

An Architectural Decision (**AD**) is a software design choice that addresses a functional or non-functional requirement that is architecturally significant.
An Architecturally Significant Requirement (**ASR**) is a requirement that has a measurable effect on a software system’s architecture and quality.
An Architectural Decision Record (**ADR**) captures a single AD, such as often done when writing personal notes or meeting minutes; the collection of ADRs created and maintained in a project constitute its decision log. All these are within the topic of Architectural Knowledge Management (AKM).

You can read more about the ADR concept in this [blog post](https://product.reverb.com/documenting-architecture-decisions-the-reverb-way-a3563bb24bd0#.78xhdix6t).

## Rationale

ADRs are intended to be the primary mechanism for proposing new feature designs and new processes, for collecting community input on an issue, and for documenting the design decisions.
An ADR should provide:

* Context on the relevant goals and the current state
* Proposed changes to achieve the goals
* Summary of pros and cons
* References
* Changelog

Note the distinction between an ADR and a spec. The ADR provides the context, intuition, reasoning, and
justification for a change in architecture, or for the architecture of something
new. The spec is a much more compressed and streamlined summary of everything as
it stands today.

If recorded decisions turned out to be lacking, convene a discussion, record the new decisions here, and then modify the code to match.

## Creating new ADR

Read about the [PROCESS](/sdk/latest/reference/architecture/PROCESS).

### Use RFC 2119 Keywords

When writing ADRs, follow the same best practices for writing RFCs. When writing RFCs, key words are used to signify the requirements in the specification. These words are often capitalized: "MUST," "MUST NOT," "REQUIRED," "SHALL," "SHALL NOT," "SHOULD," "SHOULD NOT," "RECOMMENDED," "MAY," and "OPTIONAL." They are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## ADR Table of Contents

### Accepted EXEMPLO

* [ADR XXX: Nome Do ADR](/sdk/latest/reference/architecture/adr-002-docs-structure)


### Proposed EXEMPLO

* [ADR 003: Dynamic Capability Store](/sdk/latest/reference/architecture/adr-003-dynamic-capability-store)
* [ADR 011: Generalize Genesis Accounts](/sdk/latest/reference/architecture/adr-011-generalize-genesis-accounts)


### Draft EXEMPLO

* [ADR 044: Guidelines for Updating Protobuf Definitions](/sdk/latest/reference/architecture/adr-044-protobuf-updates-guidelines)
* [ADR 047: Extend Upgrade Plan](/sdk/latest/reference/architecture/adr-047-extend-upgrade-plan)
* [ADR 053: Go Module Refactoring](/sdk/latest/reference/architecture/adr-053-go-module-refactoring)
* [ADR 068: Preblock](/sdk/latest/reference/architecture/adr-068-preblock)


## ADR Index EXEMPLO

### Hardware and firmware EXEMPLO

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-012](ADR-012-esp32-csi-sensor-mesh.md) | ESP32 CSI Sensor Mesh for Distributed Sensing | Accepted (partial) |
| [ADR-018](ADR-018-esp32-dev-implementation.md) | ESP32 Development Implementation Path | Proposed |
| [ADR-028](ADR-028-esp32-capability-audit.md) | ESP32 Capability Audit and Witness Record | Accepted |
| [ADR-029](ADR-029-ruvsense-multistatic-sensing-mode.md) | RuvSense Multistatic Sensing Mode (TDM, channel hopping) | Proposed |
| [ADR-032](ADR-032-multistatic-mesh-security-hardening.md) | Multistatic Mesh Security Hardening | Accepted |
| [ADR-039](ADR-039-esp32-edge-intelligence.md) | ESP32-S3 Edge Intelligence Pipeline (on-device vitals) | Accepted (hardware-validated) |
| [ADR-040](ADR-040-wasm-programmable-sensing.md) | WASM Programmable Sensing (Tier 3) | Accepted |
| [ADR-041](ADR-041-wasm-module-collection.md) | WASM Module Collection (65 edge modules) | Accepted (hardware-validated) |
| [ADR-044](ADR-044-provisioning-tool-enhancements.md) | Provisioning Tool Enhancements | Proposed |
| [ADR-110](ADR-110-esp32-c6-firmware-extension.md) | ESP32-C6 firmware extension — Wi-Fi 6 / 802.15.4 / TWT / LP-core | Accepted, P1-P10 complete, firmware-side substrate closed at **[v0.7.0-esp32](https://github.com/ruvnet/RuView/releases/tag/v0.7.0-esp32)**. Companion docs: [`WITNESS-LOG-110`](../WITNESS-LOG-110.md) (13 §A0.x entries · 99.56 % cross-board RX · **104.1 µs smoothed sync stdev** · ≤100 µs target met), [`ADR-110-REVIEW-GUIDE`](../ADR-110-REVIEW-GUIDE.md) (one-page reviewer tour), [`ADR-110-BRANCH-STATE`](../ADR-110-BRANCH-STATE.md) (coordination map vs `feat/adr-115-ha-mqtt-matter`). Host decoders + tests: Python `SyncPacketParser` (10) + Rust `wifi_densepose_hardware::SyncPacket` (15), cross-language hex pin gates drift. |
