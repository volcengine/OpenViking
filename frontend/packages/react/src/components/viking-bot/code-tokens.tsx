import { useEffect, useMemo, useState } from 'react';
import {
  Prism,
  loadPrismLanguage,
  prismLanguage,
} from '../../shared/prism-language.js';

function renderToken(token: string | Prism.Token, index: number) {
  if (typeof token === 'string') return token;
  const classes = [token.type, ...[token.alias ?? []].flat()];
  return (
    <span
      key={index}
      className={classes
        .map((name) => `ov-viking-bot__token--${name}`)
        .join(' ')}
    >
      {[token.content].flat().map(renderToken)}
    </span>
  );
}

export function useCodeTokens(code: string, hint: string, enabled: boolean) {
  const language = prismLanguage({ fileName: '', fileType: hint });
  const [loaded, setLoaded] = useState<{
    language: string;
    grammar: Prism.Grammar;
  }>();
  // Effect necessity: lazily register the requested Prism grammar and ignore
  // a previous request if the language changes during streaming.
  useEffect(() => {
    if (!language || language === 'plaintext' || !enabled) return;
    let current = true;
    void loadPrismLanguage(language)
      .then((grammar) => {
        if (current && grammar) setLoaded({ language, grammar });
      })
      .catch(() => {
        // Display the original code if the optional grammar fails to load.
      });
    return () => {
      current = false;
    };
  }, [language, enabled]);
  return useMemo(() => {
    if (!enabled || loaded?.language !== language) return code;
    return Prism.tokenize(code, loaded.grammar).map(renderToken);
  }, [code, enabled, language, loaded]);
}
