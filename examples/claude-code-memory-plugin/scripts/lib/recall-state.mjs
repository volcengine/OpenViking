export function topScoreFromContextBlock(block) {
  let topScore = 0;
  for (const match of String(block || "").matchAll(/\bscore="([^"]+)"/g)) {
    const score = Number(match[1]);
    if (Number.isFinite(score) && score >= 0 && score <= 1) {
      topScore = Math.max(topScore, score);
    }
  }
  return topScore;
}
