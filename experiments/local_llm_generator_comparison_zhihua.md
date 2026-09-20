# Local LLM Generator Comparison: Zhihua Temple Ceiling

**Date:** 2026-09-20  
**Project:** Provenance Wiki  
**Experiment type:** Generator-only comparison  
**Question:** *Does Laurence Sickman have any ethical dilemma regarding the purchase of the ceiling from Zhihua Temple?*

## Experimental Setup

The purpose of this test is to compare the final answer-generation layer while holding the rest of the Provenance Wiki pipeline constant.

### Shared conditions

- Same Provenance Wiki database
- Same retrieval pipeline
- Same retrieved evidence
- Same context construction
- Same system prompt
- Same user question
- Same citation parsing and response structure

### Claude configuration

- Provider: Claude
- Model: existing Provenance Wiki Claude configuration
- Retrieval and context: unchanged

### Local configuration

- Provider: Ollama
- Model: `qwen3:14b`
- GPU: NVIDIA GeForce RTX 5070 Ti, 16 GB VRAM
- Ollama context: 32,768 tokens
- Ollama reported model size during inference: ~14 GB
- Processor: 100% GPU
- Thinking mode: disabled for this comparison

---

## User Question

> does laruence sickman have any ethical dilemma regarding the purchase of the ceiling from Zhihua temple?

---

## Qwen3 14B Local Answer

> **A**
>
> The documents do not explicitly mention any ethical dilemma regarding Laurence Sickman's purchase of the ceiling from Zhihua Temple. However, they do provide some context that could be interpreted as raising ethical concerns:
>
> 1. **Dr. Ecke's Involvement**: In [Doc #317], it is mentioned that Dr. Ecke had conducted a survey of the original temple from which the ceiling was taken and planned to publish a monograph on the temple, including the Gallery ceiling. This suggests that the ceiling was removed from a historically significant site, and Dr. Ecke had a scholarly interest in documenting the temple's architecture.
>
> 2. **Dr. Ecke's Request for Credit**: In [Group #151], it is noted that Dr. Ecke provided a report on the temple but explicitly requested that his name not be associated with the transfer of the ceiling. This could imply that Dr. Ecke was uncomfortable with the removal of the ceiling or wanted to avoid any implication of endorsing the transfer.
>
> 3. **Lack of Explicit Ethical Concerns**: Despite the above points, there is no direct mention in the documents that Laurence Sickman faced or expressed any ethical dilemma regarding the purchase of the ceiling. The documents focus on logistical and artistic considerations rather than ethical ones.
>
> In summary, while the documents suggest that the ceiling was removed from a historically significant temple and that Dr. Ecke had a scholarly interest in the site, there is no explicit indication that Laurence Sickman faced an ethical dilemma regarding the purchase.
>
> **Sources**
>
> Doc #317: Letter regarding Chinese …
>
> Group #151: Multi-page letter from La…

---

## Claude Answer

> ## Ethical Dimensions of the Zhihua Temple Ceiling Acquisition
>
> Based on the available documents, there is **one significant ethical concern** documented in the primary-source evidence, though it centers more on **concealment and institutional conflict of interest** than on a dilemma Sickman himself openly wrestled with.
>
> ---
>
> ### The Core Issue: Dr. Ecke's Concealment
>
> The clearest ethical problem is documented in the postscript of Sickman's April 1933 letter [Group #151]:
>
> > *"Dr. Ecke especially requests that his name is not mentioned in connection with the ceiling. Dr. Ecke is a member of the Chinese society for the preservation and restoration of ancient architectural monuments, and is, as a consequence, supposed to know nothing about the ceiling being in America."*
>
> Sickman then adds a notably **casual rationalization**:
>
> > *"The Chinese are often a little vague in these matters and would, in all likelihood, overlook the fact that the ceiling had been removed some time ago and had for two years been on the open market."*
>
> This passage reveals several ethically significant facts:
>
> - Dr. Ecke, a member of a **Chinese preservation society**, had provided technical plans for the ceiling's installation while being obligated to protect such monuments
> - The ceiling had been **removed from Chih Hua Ssu (Zhihua Temple)** — identified as *"one of the few remaining examples of Ming architecture in Peking"* [Doc #317, Group #88] — and placed on the open market
> - There was a conscious effort to **suppress Dr. Ecke's involvement** to avoid accountability
>
> ---
>
> ### Sickman's Own Stance
>
> Sickman does **not** express personal moral conflict in the available documents. His framing — *"the Chinese are often a little vague in these matters"* — suggests he **minimized or deflected** the ethical weight of the situation rather than confronting it. There is no document in the provided sources where Sickman directly acknowledges any personal dilemma about acquiring the ceiling itself.
>
> ---
>
> ### Summary
>
> The available documents do not show Sickman experiencing an explicit ethical dilemma about the ceiling purchase. What **is** documented is:
>
> 1. Awareness that the ceiling's removal was ethically and institutionally problematic (at least for Dr. Ecke)
> 2. A deliberate effort to conceal Dr. Ecke's advisory role
> 3. A dismissive rationalization of the broader concern about removing architectural heritage from China
>
> The ethical problem is **present in the record** [Group #151], but Sickman appears to have **observed rather than agonized over** it.
>
> **Sources**
>
> Group #151: Multi-page letter from La…

---

## Comparison

Both models reach the most important evidentiary conclusion:

> The available documents do **not** explicitly show Sickman experiencing or expressing a personal ethical dilemma about purchasing the Zhihua Temple ceiling.

The difference lies primarily in **interpretive reach** and **interpretive calibration**.

### Qwen3 14B

**Strengths**

- Preserves the distinction between explicit evidence and interpretation.
- Does not claim that Sickman personally experienced a moral conflict.
- Uses qualified language such as "could be interpreted" and "could imply."
- Avoids inventing an acquisition outcome or undocumented motive.
- Provides a comparatively cautious provenance-research answer.

**Potential overreach**

The sentence:

> "This could imply that Dr. Ecke was uncomfortable with the removal of the ceiling..."

is plausible, but the document establishes only that Ecke asked not to have his name associated with the ceiling. It does not establish *why* he made that request.

A more disciplined formulation would be:

> "This suggests that Ecke regarded association with the transfer as sensitive, although the document does not establish his reasons."

### Claude

**Strengths**

- Identifies the strongest primary-source passage more directly.
- Draws attention to Ecke's membership in a Chinese preservation organization.
- Quotes Sickman's own statement about Ecke being "supposed to know nothing about the ceiling being in America."
- Produces a richer and more historically provocative interpretation.
- Correctly states that Sickman does not explicitly express personal moral conflict.

**Potential overreach**

Several formulations move from plausible interpretation toward asserted historical fact:

| Claude wording | Evidentiary concern |
|---|---|
| "concealment and institutional conflict of interest" | "Conflict of interest" is an analytical characterization not explicitly established by the letter. |
| Ecke was "obligated to protect such monuments" | Membership in a preservation society does not by itself establish the precise obligation asserted. |
| "suppress Dr. Ecke's involvement to avoid accountability" | The request for anonymity is documented; the purpose "to avoid accountability" is inferred. |
| Sickman "minimized or deflected the ethical weight" | Plausible interpretation, but it attributes an attitude or motive. |
| awareness that removal was "ethically and institutionally problematic" | The letter establishes sensitivity around Ecke's association; it does not explicitly state Sickman's ethical judgment of the removal. |
| "dismissive rationalization" | This is a characterization of Sickman's rhetoric rather than a documentary fact. |
| Sickman "observed rather than agonized over" the problem | Rhetorically effective but psychologically stronger than the evidence permits. |

---

## Preliminary Evaluation

| Dimension | Qwen3 14B | Claude |
|---|---|---|
| Correct overall conclusion | Excellent | Excellent |
| Identification of important evidence | Good | Excellent |
| Source grounding | Very good | Very good |
| Interpretive depth | Good | Excellent |
| Calibration of inference | Very good | Fair–Good |
| Risk of unsupported historical claim | Low | Noticeably higher |
| Usefulness to researcher | Very good | Very good, but requires closer scrutiny |

### Preliminary finding

This case suggests a productive distinction between:

- **Interpretive reach** — how far the model develops a historically meaningful interpretation.
- **Interpretive calibration** — how carefully the model preserves the boundary between documentary fact, reasonable inference, and unsupported attribution of motive or attitude.

In this example, Claude is more analytically ambitious, but Qwen3 14B is more conservative about the limits of the documentary evidence.

---

## Example of a More Calibrated Answer

> The available evidence does not show Sickman explicitly wrestling with an ethical dilemma over acquiring the Zhihua Temple ceiling. It does, however, show that he was aware of sensitivity surrounding the transaction. In an April 1933 letter, Sickman reports that Dr. Ecke "especially requests that his name is not mentioned in connection with the ceiling," explaining that Ecke belonged to a Chinese society for the preservation and restoration of ancient architectural monuments and was therefore "supposed to know nothing about the ceiling being in America." Sickman then remarks that "the Chinese are often a little vague in these matters" and might overlook the fact that the ceiling had already been removed and on the market for two years.
>
> These passages establish that Ecke's association with the transfer was considered sensitive and that Sickman knew this. They may also suggest that Sickman was rationalizing the situation, but the documents do not establish why Ecke wanted anonymity or demonstrate that Sickman himself regarded the purchase as ethically wrong. The evidence therefore supports **awareness of a sensitive provenance situation**, rather than a documented personal ethical dilemma.

---

## Evaluation Principle for Future Tests

A useful conceptual model for future generator comparisons is:

```text
document says X
        ↓
X supports inference Y
        ↓
Y does NOT necessarily prove motive / attitude Z
```

Future comparison questions should therefore evaluate not only factual correctness, but also whether each model:

1. distinguishes explicit documentary evidence from inference;
2. qualifies uncertain interpretations appropriately;
3. avoids attributing motives, attitudes, intentions, or institutional roles without sufficient evidence;
4. provides interpretations that remain useful to a provenance researcher;
5. cites the primary-source evidence supporting each interpretive step.
