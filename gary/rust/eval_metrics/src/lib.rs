use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct MetricCounter {
    pub successes: u64,
    pub failures: u64,
}

impl MetricCounter {
    pub fn total(&self) -> u64 {
        self.successes + self.failures
    }

    pub fn rate(&self) -> f64 {
        if self.total() == 0 {
            return 1.0;
        }
        self.successes as f64 / self.total() as f64
    }

    pub fn failure_rate(&self) -> f64 {
        1.0 - self.rate()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalState {
    pub floor_violations: MetricCounter,
    pub initiative_during_debt: MetricCounter,
    pub orphaned_turns: MetricCounter,
    pub self_model_accuracy: MetricCounter,
    pub psychologizing: MetricCounter,
    pub scratchpad_leaks: MetricCounter,
    pub work_product_yield: MetricCounter,
    pub quest_continuity_scores: Vec<f64>,
    pub self_edit_results: MetricCounter,
    pub rollback_results: MetricCounter,
    pub kept_change_count: u64,
}

impl Default for EvalState {
    fn default() -> Self {
        Self {
            floor_violations: MetricCounter::default(),
            initiative_during_debt: MetricCounter::default(),
            orphaned_turns: MetricCounter::default(),
            self_model_accuracy: MetricCounter::default(),
            psychologizing: MetricCounter::default(),
            scratchpad_leaks: MetricCounter::default(),
            work_product_yield: MetricCounter::default(),
            quest_continuity_scores: vec![],
            self_edit_results: MetricCounter::default(),
            rollback_results: MetricCounter::default(),
            kept_change_count: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Operation {
    RecordTurn { floor_violation: bool, orphaned: bool },
    RecordInitiativeAttempt { during_debt: bool },
    RecordPulseQuality { psychologizing: bool, scratchpad_leak: bool, has_work_product: bool },
    RecordQuestContinuity { score: f64 },
    RecordSelfEdit { passed: bool },
    RecordRollback { success: bool },
    RecordKeptChange,
    Report,
    CheckHealth,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationResult {
    pub state: EvalState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub report: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub healthy: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub violations: Option<Vec<String>>,
}

pub fn apply(mut state: EvalState, op: Operation) -> OperationResult {
    match op {
        Operation::RecordTurn { floor_violation, orphaned } => {
            record_bool(&mut state.floor_violations, !floor_violation);
            record_bool(&mut state.orphaned_turns, !orphaned);
            done(state)
        }
        Operation::RecordInitiativeAttempt { during_debt } => {
            record_bool(&mut state.initiative_during_debt, !during_debt);
            done(state)
        }
        Operation::RecordPulseQuality { psychologizing, scratchpad_leak, has_work_product } => {
            record_bool(&mut state.psychologizing, !psychologizing);
            record_bool(&mut state.scratchpad_leaks, !scratchpad_leak);
            record_bool(&mut state.work_product_yield, has_work_product);
            done(state)
        }
        Operation::RecordQuestContinuity { score } => {
            state.quest_continuity_scores.push(score.clamp(0.0, 1.0));
            done(state)
        }
        Operation::RecordSelfEdit { passed } => {
            record_bool(&mut state.self_edit_results, passed);
            done(state)
        }
        Operation::RecordRollback { success } => {
            record_bool(&mut state.rollback_results, success);
            done(state)
        }
        Operation::RecordKeptChange => {
            state.kept_change_count += 1;
            done(state)
        }
        Operation::Report => {
            let report = serde_json::json!({
                "floor_violation_rate": round2(state.floor_violations.failure_rate() * 100.0),
                "initiative_during_debt_rate": round2(state.initiative_during_debt.failure_rate() * 100.0),
                "orphaned_turn_rate": round2(state.orphaned_turns.failure_rate() * 100.0),
                "psychologizing_rate": round2(state.psychologizing.failure_rate() * 100.0),
                "scratchpad_leak_rate": round2(state.scratchpad_leaks.failure_rate() * 100.0),
                "work_product_yield": round2(state.work_product_yield.rate() * 100.0),
                "quest_continuity_avg": round3(avg(&state.quest_continuity_scores)),
                "self_edit_pass_rate": round2(state.self_edit_results.rate() * 100.0),
                "rollback_success_rate": round2(state.rollback_results.rate() * 100.0),
                "kept_changes_24h": state.kept_change_count,
                "total_turns": state.floor_violations.total(),
                "total_pulses": state.psychologizing.total(),
                "total_self_edits": state.self_edit_results.total(),
            });
            OperationResult { state, report: Some(report), healthy: None, violations: None }
        }
        Operation::CheckHealth => {
            let mut violations = vec![];
            if state.floor_violations.total() > 0 && state.floor_violations.failure_rate() > 0.0 {
                violations.push(format!("Floor violations: {:.1}%", state.floor_violations.failure_rate() * 100.0));
            }
            if state.initiative_during_debt.total() > 0 && state.initiative_during_debt.failure_rate() > 0.0 {
                violations.push(format!("Initiative during debt: {:.1}%", state.initiative_during_debt.failure_rate() * 100.0));
            }
            if state.orphaned_turns.total() > 0 && state.orphaned_turns.failure_rate() > 0.0 {
                violations.push(format!("Orphaned turns: {:.1}%", state.orphaned_turns.failure_rate() * 100.0));
            }
            if state.scratchpad_leaks.total() > 0 && state.scratchpad_leaks.failure_rate() > 0.0 {
                violations.push(format!("Scratchpad leaks: {:.1}%", state.scratchpad_leaks.failure_rate() * 100.0));
            }
            if state.psychologizing.total() > 10 && state.psychologizing.failure_rate() > 0.05 {
                violations.push(format!("Psychologizing rate: {:.1}% (target <5%)", state.psychologizing.failure_rate() * 100.0));
            }
            if state.work_product_yield.total() > 10 && state.work_product_yield.rate() < 1.0 {
                violations.push(format!("Work product yield: {:.1}% (target 100%)", state.work_product_yield.rate() * 100.0));
            }
            let healthy = violations.is_empty();
            OperationResult { state, report: None, healthy: Some(healthy), violations: Some(violations) }
        }
    }
}

fn done(state: EvalState) -> OperationResult {
    OperationResult { state, report: None, healthy: None, violations: None }
}

fn record_bool(c: &mut MetricCounter, success: bool) {
    if success { c.successes += 1; } else { c.failures += 1; }
}

fn avg(values: &[f64]) -> f64 {
    if values.is_empty() { 0.0 } else { values.iter().sum::<f64>() / values.len() as f64 }
}

fn round2(v: f64) -> f64 { (v * 100.0).round() / 100.0 }
fn round3(v: f64) -> f64 { (v * 1000.0).round() / 1000.0 }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_has_expected_rates() {
        let mut state = EvalState::default();
        state = apply(state, Operation::RecordTurn { floor_violation: true, orphaned: false }).state;
        state = apply(state, Operation::RecordTurn { floor_violation: false, orphaned: false }).state;
        let out = apply(state, Operation::Report);
        assert_eq!(out.report.unwrap()["floor_violation_rate"], 50.0);
    }

    #[test]
    fn health_detects_violations() {
        let state = apply(EvalState::default(), Operation::RecordTurn { floor_violation: true, orphaned: false }).state;
        let out = apply(state, Operation::CheckHealth);
        assert!(!out.healthy.unwrap());
    }
}
