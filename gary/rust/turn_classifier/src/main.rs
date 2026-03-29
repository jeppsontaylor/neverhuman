use std::io::{self, Read};

use gary_turn_classifier::{classify_turn, classify_turn_v2};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Input {
    #[serde(default)]
    command: String,
    text: String,
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

    let output = if input.command == "v2" {
        serde_json::to_string(&classify_turn_v2(&input.text))
    } else {
        serde_json::to_string(&classify_turn(&input.text))
    };

    match output {
        Ok(s) => println!("{s}"),
        Err(err) => {
            eprintln!("failed to encode output: {err}");
            std::process::exit(3);
        }
    }
}
