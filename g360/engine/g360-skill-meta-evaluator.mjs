export async function evaluateMetaTags(html) {
  const results = {
    score: 0,
    tags: [],
    issues: [],
    suggestions: []
  };

  const requiredTags = ['title', 'description', 'viewport'];
  const metaTags = html.match(/<meta[^>]+>/gi) || [];
  
  requiredTags.forEach(tag => {
    if (html.includes(`name="${tag}"`) || html.includes(`property="${tag}"`)) {
      results.tags.push({ tag, found: true });
    } else {
      results.issues.push({ tag, message: `Missing ${tag} tag` });
    }
  });

  const ogTags = ['og:title', 'og:description', 'og:image'];
  ogTags.forEach(tag => {
    if (html.includes(`property="${tag}"`)) {
      results.tags.push({ tag, found: true, type: 'og' });
    } else {
      results.suggestions.push({ tag, message: `Consider adding Open Graph: ${tag}` });
    }
  });

  results.score = Math.round((results.tags.length / (requiredTags.length + ogTags.length)) * 100);
  return results;
}

export default { evaluateMetaTags };
