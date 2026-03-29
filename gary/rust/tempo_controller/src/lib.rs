use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TempoMode {
    Snap,
    Quick,
    Deep,
    Explore,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextPack {
    Micro,
    Standard,
    Deep,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelRoute {
    SidecarOnly,
    SidecarThen35b,
    Main35bDirect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnMode {
    Snap,
    Layered,
    Deep,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TurnContract {
    pub mode: TempoMode,
    pub context_pack: ContextPack,
    pub model_route: ModelRoute,
    pub first_sentence_max_words: u8,
    pub max_sentences: u8,
    pub answer_first: bool,
    pub progressive_tts: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TurnPolicy {
    pub turn_mode: TurnMode,
    pub tempo_contract: TurnContract,
    pub llm_max_tokens: u16,
    pub llm_temperature: f32,
    pub should_play_filler: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TurnInput {
    pub text: String,
    #[serde(default)]
    pub has_external_lookup: bool,
}

pub fn build_contract(input: &TurnInput) -> TurnContract {
    let mode = classify_mode(&input.text, input.has_external_lookup);
    match mode {
        TempoMode::Snap => TurnContract {
            mode,
            context_pack: ContextPack::Micro,
            model_route: ModelRoute::SidecarOnly,
            first_sentence_max_words: 7,
            max_sentences: 2,
            answer_first: true,
            progressive_tts: true,
        },
        TempoMode::Quick => TurnContract {
            mode,
            context_pack: ContextPack::Standard,
            model_route: ModelRoute::SidecarThen35b,
            first_sentence_max_words: 10,
            max_sentences: 3,
            answer_first: true,
            progressive_tts: true,
        },
        TempoMode::Deep => TurnContract {
            mode,
            context_pack: ContextPack::Deep,
            model_route: ModelRoute::Main35bDirect,
            first_sentence_max_words: 12,
            max_sentences: 6,
            answer_first: true,
            progressive_tts: true,
        },
        TempoMode::Explore => TurnContract {
            mode,
            context_pack: ContextPack::Deep,
            model_route: ModelRoute::SidecarThen35b,
            first_sentence_max_words: 12,
            max_sentences: 5,
            answer_first: true,
            progressive_tts: true,
        },
    }
}

pub fn to_turn_mode(contract: &TurnContract) -> TurnMode {
    match contract.mode {
        TempoMode::Snap => TurnMode::Snap,
        TempoMode::Quick => TurnMode::Layered,
        TempoMode::Deep | TempoMode::Explore => TurnMode::Deep,
    }
}

pub fn llm_params_for_contract(contract: &TurnContract) -> (u16, f32) {
    match contract.mode {
        TempoMode::Snap => (120, 0.55),
        TempoMode::Quick => (320, 0.70),
        TempoMode::Deep => (800, 0.72),
        TempoMode::Explore => (700, 0.90),
    }
}

pub fn build_policy(input: &TurnInput) -> TurnPolicy {
    let contract = build_contract(input);
    let mode = to_turn_mode(&contract);
    let (llm_max_tokens, llm_temperature) = llm_params_for_contract(&contract);

    TurnPolicy {
        turn_mode: mode,
        tempo_contract: contract,
        llm_max_tokens,
        llm_temperature,
        should_play_filler: mode != TurnMode::Snap,
    }
}

pub fn classify_mode(text: &str, has_external_lookup: bool) -> TempoMode {
    let lower = text.trim().to_lowercase();
    let wc = lower.split_whitespace().count();

    if lower.is_empty() {
        return TempoMode::Snap;
    }

    let deep_kw = [
        "implement",
        "architecture",
        "step by step",
        "debug",
        "refactor",
        "compare",
        "tradeoff",
        "design",
        "algorithm",
        "prove",
        "explain in detail",
    ];

    let explore_kw = [
        "brainstorm",
        "novel",
        "hypothesis",
        "counterfactual",
        "experiment",
        "research",
        "new idea",
        "what if",
    ];

    if explore_kw.iter().any(|k| lower.contains(k)) {
        return TempoMode::Explore;
    }

    if has_external_lookup {
        return TempoMode::Deep;
    }

    if wc >= 32 || deep_kw.iter().any(|k| lower.contains(k)) {
        return TempoMode::Deep;
    }
    if wc <= 5 {
        return TempoMode::Snap;
    }

    TempoMode::Quick
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snap_for_short_turns() {
        assert_eq!(classify_mode("yes", false), TempoMode::Snap);
        assert_eq!(classify_mode("what time is it", false), TempoMode::Snap);
    }

    #[test]
    fn deep_for_explicit_complexity() {
        assert_eq!(
            classify_mode("please implement quicksort in rust", false),
            TempoMode::Deep
        );
    }

    #[test]
    fn explore_for_novelty_requests() {
        assert_eq!(
            classify_mode("brainstorm a counterfactual hypothesis", false),
            TempoMode::Explore
        );
    }

    #[test]
    fn policy_has_budget_and_mode() {
        let p = build_policy(&TurnInput {
            text: "please implement a robust controller for this".to_string(),
            has_external_lookup: false,
        });
        assert_eq!(p.turn_mode, TurnMode::Deep);
        assert!(p.llm_max_tokens >= 320);
    }
}
