/// Regex canonicalization oracle using `regex-syntax` HIR.
///
/// Parses patterns into HIR, strips capture groups (which are structurally
/// insignificant for matching semantics), then compares canonical forms.
///
/// Input:  ["pattern1", "pattern2", ...]
/// Output: [{"pattern": "...", "canonical": "...", "error": null}, ...]
use std::io::{self, Read};

use regex_syntax::ast::parse::Parser;
use regex_syntax::hir::translate::Translator;
use regex_syntax::hir::{Hir, HirKind};

/// Recursively strip capture groups from HIR, leaving only the inner content.
/// This normalizes `(x)+` and `x+` to the same representation.
fn strip_captures(hir: Hir) -> Hir {
    match hir.into_kind() {
        HirKind::Capture(cap) => strip_captures(*cap.sub),
        HirKind::Repetition(mut rep) => {
            rep.sub = Box::new(strip_captures(*rep.sub));
            Hir::repetition(rep)
        }
        HirKind::Concat(subs) => {
            Hir::concat(subs.into_iter().map(strip_captures).collect())
        }
        HirKind::Alternation(subs) => {
            Hir::alternation(subs.into_iter().map(strip_captures).collect())
        }
        // Leaves: Literal, Class, Look, Empty — no children to transform
        other => {
            // Reconstruct Hir from HirKind using the specific constructors
            match other {
                HirKind::Empty => Hir::empty(),
                HirKind::Literal(lit) => Hir::literal(lit.0),
                HirKind::Class(cls) => Hir::class(cls),
                HirKind::Look(look) => Hir::look(look),
                _ => unreachable!(),
            }
        }
    }
}

fn canonicalize(pattern: &str) -> Result<String, String> {
    let ast = Parser::new().parse(pattern).map_err(|e| e.to_string())?;
    let hir = Translator::new()
        .translate(pattern, &ast)
        .map_err(|e| e.to_string())?;
    let normalized = strip_captures(hir);
    Ok(normalized.to_string())
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();

    let patterns: Vec<String> = match serde_json::from_str(&input) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("JSON parse error: {e}");
            std::process::exit(1);
        }
    };
    let mut results: Vec<serde_json::Value> = Vec::new();

    for pattern in &patterns {
        match canonicalize(pattern) {
            Ok(canonical) => {
                results.push(serde_json::json!({
                    "pattern": pattern,
                    "canonical": canonical,
                    "error": null
                }));
            }
            Err(e) => {
                results.push(serde_json::json!({
                    "pattern": pattern,
                    "canonical": null,
                    "error": e
                }));
            }
        }
    }

    println!("{}", serde_json::to_string(&results).unwrap());
}
