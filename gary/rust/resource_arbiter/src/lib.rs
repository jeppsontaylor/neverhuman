use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    Reflex,
    Mind,
    Forge,
    Replay,
    Indexing,
    Discovery,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourcePriority {
    Critical = 0,
    High = 1,
    Normal = 2,
    Low = 3,
    Idle = 4,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResourceClaim {
    pub kind: ResourceKind,
    pub priority: ResourcePriority,
    pub task_id: String,
    pub paused: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArbiterState {
    pub claims: BTreeMap<String, ResourceClaim>,
    pub ttft_samples: Vec<f64>,
    pub max_samples: usize,
    pub threshold_ms: f64,
    pub circuit_broken: bool,
}

impl Default for ArbiterState {
    fn default() -> Self {
        Self {
            claims: BTreeMap::new(),
            ttft_samples: Vec::new(),
            max_samples: 50,
            threshold_ms: 2000.0,
            circuit_broken: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Operation {
    RegisterClaim {
        kind: ResourceKind,
        task_id: String,
        priority: ResourcePriority,
    },
    ReleaseClaim {
        task_id: String,
    },
    OnUserActive,
    OnUserIdle,
    OnOnset,
    RecordTtft {
        ttft_ms: f64,
    },
    ShouldAllow {
        kind: ResourceKind,
    },
    Status,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationResult {
    pub state: ArbiterState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub paused_task_ids: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resumed_task_ids: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub allow: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<serde_json::Value>,
}

pub fn apply(mut state: ArbiterState, op: Operation) -> OperationResult {
    match op {
        Operation::RegisterClaim {
            kind,
            task_id,
            priority,
        } => {
            let paused = state.circuit_broken && (priority as i32) > (ResourcePriority::Critical as i32);
            let claim = ResourceClaim {
                kind,
                priority,
                task_id: task_id.clone(),
                paused,
            };
            state.claims.insert(task_id.clone(), claim);
            OperationResult {
                state,
                paused_task_ids: if paused { Some(vec![task_id]) } else { Some(vec![]) },
                resumed_task_ids: None,
                allow: None,
                status: None,
            }
        }
        Operation::ReleaseClaim { task_id } => {
            state.claims.remove(&task_id);
            OperationResult {
                state,
                paused_task_ids: None,
                resumed_task_ids: None,
                allow: None,
                status: None,
            }
        }
        Operation::OnUserActive => {
            let mut paused = vec![];
            for (task_id, claim) in state.claims.iter_mut() {
                if (claim.priority as i32) > (ResourcePriority::High as i32) && !claim.paused {
                    claim.paused = true;
                    paused.push(task_id.clone());
                }
            }
            OperationResult {
                state,
                paused_task_ids: Some(paused),
                resumed_task_ids: None,
                allow: None,
                status: None,
            }
        }
        Operation::OnUserIdle => {
            if state.circuit_broken {
                return OperationResult {
                    state,
                    paused_task_ids: None,
                    resumed_task_ids: Some(vec![]),
                    allow: None,
                    status: None,
                };
            }
            let mut resumed = vec![];
            for (task_id, claim) in state.claims.iter_mut() {
                if claim.paused {
                    claim.paused = false;
                    resumed.push(task_id.clone());
                }
            }
            OperationResult {
                state,
                paused_task_ids: None,
                resumed_task_ids: Some(resumed),
                allow: None,
                status: None,
            }
        }
        Operation::OnOnset => {
            let mut paused = vec![];
            for (task_id, claim) in state.claims.iter_mut() {
                if claim.kind != ResourceKind::Reflex && !claim.paused {
                    claim.paused = true;
                    paused.push(task_id.clone());
                }
            }
            OperationResult {
                state,
                paused_task_ids: Some(paused),
                resumed_task_ids: None,
                allow: None,
                status: None,
            }
        }
        Operation::RecordTtft { ttft_ms } => {
            state.ttft_samples.push(ttft_ms);
            if state.ttft_samples.len() > state.max_samples {
                let start = state.ttft_samples.len() - state.max_samples;
                state.ttft_samples = state.ttft_samples[start..].to_vec();
            }
            let was_broken = state.circuit_broken;
            state.circuit_broken = is_degraded(&state);

            if state.circuit_broken && !was_broken {
                for claim in state.claims.values_mut() {
                    if (claim.priority as i32) > (ResourcePriority::Critical as i32) {
                        claim.paused = true;
                    }
                }
            } else if !state.circuit_broken && was_broken {
                for claim in state.claims.values_mut() {
                    if claim.paused {
                        claim.paused = false;
                    }
                }
            }

            OperationResult {
                state,
                paused_task_ids: None,
                resumed_task_ids: None,
                allow: None,
                status: None,
            }
        }
        Operation::ShouldAllow { kind } => {
            let allow = !(state.circuit_broken && kind != ResourceKind::Reflex);
            OperationResult {
                state,
                paused_task_ids: None,
                resumed_task_ids: None,
                allow: Some(allow),
                status: None,
            }
        }
        Operation::Status => {
            let p95 = p95(&state.ttft_samples);
            let paused_claims = state.claims.values().filter(|c| c.paused).count();
            let claims = state
                .claims
                .iter()
                .map(|(tid, c)| {
                    (
                        tid.clone(),
                        serde_json::json!({
                            "kind": c.kind,
                            "priority": c.priority as i32,
                            "paused": c.paused,
                        }),
                    )
                })
                .collect::<BTreeMap<String, serde_json::Value>>();

            let status = serde_json::json!({
                "circuit_broken": state.circuit_broken,
                "ttft_p95_ms": (p95 * 10.0).round() / 10.0,
                "active_claims": state.claims.len(),
                "paused_claims": paused_claims,
                "claims": claims,
            });
            OperationResult {
                state,
                paused_task_ids: None,
                resumed_task_ids: None,
                allow: None,
                status: Some(status),
            }
        }
    }
}

fn p95(samples: &[f64]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let mut sorted = samples.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let idx = (sorted.len() as f64 * 0.95) as usize;
    sorted[idx.min(sorted.len() - 1)]
}

fn is_degraded(state: &ArbiterState) -> bool {
    state.ttft_samples.len() >= 5 && p95(&state.ttft_samples) > state.threshold_ms
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_active_pauses_background() {
        let state = ArbiterState::default();
        let state = apply(
            state,
            Operation::RegisterClaim {
                kind: ResourceKind::Mind,
                task_id: "mind-1".into(),
                priority: ResourcePriority::Normal,
            },
        )
        .state;
        let out = apply(state, Operation::OnUserActive);
        assert_eq!(out.paused_task_ids.unwrap(), vec!["mind-1"]);
    }

    #[test]
    fn circuit_breaks_on_degraded_ttft() {
        let mut state = ArbiterState::default();
        for _ in 0..10 {
            state = apply(state, Operation::RecordTtft { ttft_ms: 3000.0 }).state;
        }
        assert!(state.circuit_broken);
    }
}
