use std::io::{self, Read};

use gary_eval_metrics::{apply, EvalState, Operation};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Input {
    #[serde(default)]
    state: Option<EvalState>,
    operation: Operation,
}

fn main() {
    let mut buf = String::new();
    if io::stdin().read_to_string(&mut buf).is_err() {
        eprintln!("failed to read stdin");
        std::process::exit(2);
    }

    let input: Input = match serde_json::from_str(&buf) {
        Ok(v) => v,
        Err(err) => {
            eprintln!("invalid json input: {err}");
            std::process::exit(2);
        }
    };

    let state = input.state.unwrap_or_default();
    let out = apply(state, input.operation);

    match serde_json::to_string(&out) {
        Ok(s) => println!("{s}"),
        Err(err) => {
            eprintln!("failed to encode output: {err}");
            std::process::exit(3);
        }
    }
}
