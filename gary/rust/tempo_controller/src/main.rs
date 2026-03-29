use std::io::{self, Read};

use gary_tempo_controller::{build_contract, build_policy, TurnInput};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct CliInput {
    #[serde(default)]
    command: String,
    text: String,
    #[serde(default)]
    has_external_lookup: bool,
}

fn main() {
    let mut buf = String::new();
    if io::stdin().read_to_string(&mut buf).is_err() {
        eprintln!("failed to read stdin");
        std::process::exit(2);
    }

    let input: CliInput = match serde_json::from_str(&buf) {
        Ok(v) => v,
        Err(err) => {
            eprintln!("invalid json input: {err}");
            std::process::exit(2);
        }
    };

    let ti = TurnInput {
        text: input.text,
        has_external_lookup: input.has_external_lookup,
    };

    let out = if input.command == "policy" {
        serde_json::to_string(&build_policy(&ti))
    } else {
        serde_json::to_string(&build_contract(&ti))
    };

    match out {
        Ok(s) => println!("{s}"),
        Err(err) => {
            eprintln!("failed to encode output: {err}");
            std::process::exit(3);
        }
    }
}
