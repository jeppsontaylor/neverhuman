use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnMode {
    Snap,
    Layered,
    Deep,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntentClass {
    Factual,
    MetaSelf,
    ChangeRequest,
    MissionChange,
    SelfEditRequest,
    EmotionalProbe,
    Repair,
    Conversational,
    Command,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasoningMode {
    ReflexOnly,
    DeliberateBurst,
    AmbientOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TurnClassification {
    pub depth_mode: TurnMode,
    pub intent_class: IntentClass,
    pub reasoning_mode: ReasoningMode,
}

static SNAP_EXACT: &[&str] = &[
    "yes", "no", "yeah", "yep", "nope", "nah", "sure", "okay", "ok",
    "thanks", "thank you", "thank you gary", "thanks gary",
    "cool", "nice", "great", "awesome", "perfect", "got it",
    "hello", "hi", "hey", "hey gary", "hi gary", "hello gary",
    "bye", "goodbye", "see you", "later", "goodnight",
    "stop", "cancel", "never mind", "nevermind", "forget it",
    "go ahead", "continue", "keep going", "go on",
];

static SNAP_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^(?:(?:what(?:'s| is) (?:the )?(?:time|date|day|weather))|(?:how are you)|(?:tell me (?:a )?joke)|(?:that(?:'s| is) (?:all|it|enough|fine|good|great|cool)))[.?!]?\s*$",
    )
    .unwrap()
});

static DEEP_KEYWORDS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:explain|walk me through|in detail|step by step|break down|analyze|implement|code|function|class|debug|refactor|algorithm|design|architecture|compare and contrast|pros and cons|can you help me build)\b",
    )
    .unwrap()
});

static META_SELF_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:what do you (?:think|feel|know|believe|want|dream|experience)|tell me about yourself|how (?:do you|does your|are you) (?:work|think|feel|process|learn)|what(?:'s| is) (?:your|going on in your) (?:mind|brain|thought|purpose|mission|goal)|who are you|describe yourself|what are you|how were you (?:built|made|created|designed)|what(?:'s| is) your (?:architecture|system|design|code)|what (?:are|were) you (?:thinking|doing|working on)|what do you do when (?:i'm|I am) not (?:here|talking|around))\b",
    )
    .unwrap()
});

static CHANGE_REQUEST_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:change (?:your|the) (?:background|color|theme|voice|speed|tone|style)|(?:respond|talk|speak) (?:faster|slower|louder|quieter|differently)|(?:be|sound) more (?:formal|casual|friendly|professional|concise|verbose)|(?:set|adjust|update|modify) (?:your|the) (?:settings?|preferences?|options?)|(?:turn|switch) (?:on|off) (?:the )?|show (?:me )?(?:your )?(?:thoughts|mind|thinking)|hide (?:your )?(?:thoughts|mind|thinking))\b",
    )
    .unwrap()
});

static MISSION_CHANGE_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:focus (?:on|more on) (?:science|math|code|art|writing|research|learning)|(?:your|change your|update your|set your) (?:mission|focus|goal|priority|interest)|(?:be|become) (?:more|less) (?:proactive|curious|creative|cautious)|(?:i want you to|please) (?:focus|concentrate|specialize|emphasize))\b",
    )
    .unwrap()
});

static SELF_EDIT_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:add (?:a )?(?:new )?(?:feature|command|ability|capability|function)|modify (?:your|the) (?:code|behavior|logic|processing)|(?:can you|could you) (?:change|modify|update|edit) (?:your|the) (?:code|source|program)|(?:change|update|fix) (?:your|the) (?:turn detection|audio|pipeline|processing)|rewrite|patch yourself|edit yourself|update yourself)\b",
    )
    .unwrap()
});

static EMOTIONAL_PROBE_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:how (?:does|do) (?:that|this|it) make you feel|(?:are|do) you (?:happy|sad|lonely|afraid|excited|bored|frustrated|angry|scared|anxious)|what(?:'s| is) it like (?:being|to be) (?:you|an ai|artificial)|do you (?:have|experience|feel) (?:emotions?|feelings?|consciousness)|(?:are you|do you feel) (?:alive|sentient|aware|conscious)|what do you feel)\b",
    )
    .unwrap()
});

static REPAIR_PATTERNS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:(?:that(?:'s| is|was) )?(?:wrong|incorrect|not right|not what i (?:said|asked|meant))|fix (?:that|this|it)|(?:you )?(?:made a |got it |were )(?:mistake|error|wrong)|try again|redo (?:that|it)|no,? (?:i (?:said|meant|asked)))\b",
    )
    .unwrap()
});

static COMMAND_EXACT: &[&str] = &[
    "stop", "cancel", "never mind", "nevermind", "forget it",
    "shut up", "be quiet", "quiet", "mute", "unmute",
    "pause", "resume", "restart",
];

const SNAP_MAX_WORDS: usize = 5;
const DEEP_MIN_WORDS: usize = 40;

pub fn classify_turn(text: &str) -> TurnMode {
    let text = text.trim();
    if text.is_empty() {
        return TurnMode::Snap;
    }

    let lower = text.to_lowercase();
    let word_count = lower.split_whitespace().count();

    if SNAP_EXACT.contains(&lower.as_str()) {
        return TurnMode::Snap;
    }
    if SNAP_PATTERNS.is_match(&lower) {
        return TurnMode::Snap;
    }

    if DEEP_KEYWORDS.is_match(&lower) || word_count >= DEEP_MIN_WORDS {
        return TurnMode::Deep;
    }

    if word_count <= SNAP_MAX_WORDS {
        return TurnMode::Snap;
    }

    TurnMode::Layered
}

pub fn classify_turn_v2(text: &str) -> TurnClassification {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return TurnClassification {
            depth_mode: TurnMode::Snap,
            intent_class: IntentClass::Command,
            reasoning_mode: ReasoningMode::ReflexOnly,
        };
    }

    let lower = trimmed.to_lowercase();
    let depth = classify_turn(trimmed);
    let intent = classify_intent(&lower);
    let reasoning = classify_reasoning(depth, intent);

    TurnClassification {
        depth_mode: depth,
        intent_class: intent,
        reasoning_mode: reasoning,
    }
}

fn classify_intent(lower: &str) -> IntentClass {
    if COMMAND_EXACT.contains(&lower) {
        return IntentClass::Command;
    }
    if META_SELF_PATTERNS.is_match(lower) {
        return IntentClass::MetaSelf;
    }
    if EMOTIONAL_PROBE_PATTERNS.is_match(lower) {
        return IntentClass::EmotionalProbe;
    }
    if SELF_EDIT_PATTERNS.is_match(lower) {
        return IntentClass::SelfEditRequest;
    }
    if CHANGE_REQUEST_PATTERNS.is_match(lower) {
        return IntentClass::ChangeRequest;
    }
    if MISSION_CHANGE_PATTERNS.is_match(lower) {
        return IntentClass::MissionChange;
    }
    if REPAIR_PATTERNS.is_match(lower) {
        return IntentClass::Repair;
    }
    IntentClass::Conversational
}

fn classify_reasoning(depth: TurnMode, intent: IntentClass) -> ReasoningMode {
    if matches!(
        intent,
        IntentClass::MetaSelf
            | IntentClass::EmotionalProbe
            | IntentClass::SelfEditRequest
            | IntentClass::Repair
    ) {
        return ReasoningMode::DeliberateBurst;
    }

    if depth == TurnMode::Deep && intent == IntentClass::Conversational {
        return ReasoningMode::DeliberateBurst;
    }

    ReasoningMode::ReflexOnly
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snap_examples() {
        assert_eq!(classify_turn("hello"), TurnMode::Snap);
        assert_eq!(classify_turn("thanks"), TurnMode::Snap);
        assert_eq!(classify_turn("what time is it"), TurnMode::Snap);
    }

    #[test]
    fn deep_examples() {
        assert_eq!(classify_turn("please implement a binary search function"), TurnMode::Deep);
        assert_eq!(classify_turn("explain this in detail"), TurnMode::Deep);
    }

    #[test]
    fn layered_examples() {
        assert_eq!(classify_turn("tell me about the history of rome"), TurnMode::Layered);
    }

    #[test]
    fn v2_classifies_meta_self_and_reasoning() {
        let c = classify_turn_v2("What do you think about?");
        assert_eq!(c.intent_class, IntentClass::MetaSelf);
        assert_eq!(c.reasoning_mode, ReasoningMode::DeliberateBurst);
    }

    #[test]
    fn v2_classifies_command() {
        let c = classify_turn_v2("stop");
        assert_eq!(c.intent_class, IntentClass::Command);
        assert_eq!(c.reasoning_mode, ReasoningMode::ReflexOnly);
    }
}
