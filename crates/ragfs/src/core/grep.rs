use std::collections::VecDeque;

use super::types::{GrepContextLine, GrepMatch, GrepResult};

/// Collects ordered line events into grep matches with optional context.
pub(crate) struct GrepLineCollector<'a> {
    file: String,
    last_line: Option<u64>,
    before_context: usize,
    after_context: usize,
    limit: usize,
    history: VecDeque<GrepContextLine>,
    pending_after: VecDeque<usize>,
    result: &'a mut GrepResult,
}

impl<'a> GrepLineCollector<'a> {
    pub(crate) fn new(
        file: String,
        before_context: usize,
        after_context: usize,
        limit: usize,
        result: &'a mut GrepResult,
    ) -> Self {
        Self {
            file,
            last_line: None,
            before_context,
            after_context,
            limit,
            history: VecDeque::with_capacity(before_context),
            pending_after: VecDeque::new(),
            result,
        }
    }

    pub(crate) fn consume_line(
        &mut self,
        file: &str,
        line: GrepContextLine,
        is_match: bool,
    ) -> bool {
        if self.file != file
            || self
                .last_line
                .is_some_and(|last_line| line.line != last_line + 1)
        {
            self.file.clear();
            self.file.push_str(file);
            self.break_context();
        }
        self.last_line = Some(line.line);

        for &match_index in &self.pending_after {
            self.result.matches[match_index]
                .after_context
                .as_mut()
                .expect("after context is initialized when requested")
                .push(line.clone());
        }
        while self.pending_after.front().is_some_and(|&match_index| {
            self.result.matches[match_index]
                .after_context
                .as_ref()
                .is_some_and(|context| context.len() >= self.after_context)
        }) {
            self.pending_after.pop_front();
        }

        if is_match && self.result.count < self.limit {
            let match_index = self.result.matches.len();
            self.result.matches.push(GrepMatch {
                file: self.file.clone(),
                line: line.line,
                content: line.content.clone(),
                before_context: (self.before_context > 0)
                    .then(|| self.history.iter().cloned().collect()),
                after_context: (self.after_context > 0).then(Vec::new),
            });
            self.result.count += 1;
            if self.after_context > 0 {
                self.pending_after.push_back(match_index);
            }
        }

        if self.before_context > 0 {
            self.history.push_back(line);
            if self.history.len() > self.before_context {
                self.history.pop_front();
            }
        }

        !self.limit_reached()
    }

    pub(crate) fn break_context(&mut self) {
        self.history.clear();
        self.pending_after.clear();
        self.last_line = None;
    }

    pub(crate) fn limit_reached(&self) -> bool {
        self.result.count >= self.limit && self.pending_after.is_empty()
    }
}
