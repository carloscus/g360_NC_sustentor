export function validate(data, rules) {
  const results = {
    valid: true,
    errors: [],
    warnings: []
  };

  for (const [field, rule] of Object.entries(rules)) {
    const value = data[field];

    if (rule.required && (value === undefined || value === null || value === '')) {
      results.errors.push({ field, message: `${field} is required` });
      results.valid = false;
      continue;
    }

    if (rule.type && value !== undefined) {
      const actualType = Array.isArray(value) ? 'array' : typeof value;
      if (actualType !== rule.type) {
        results.errors.push({ field, message: `${field} should be ${rule.type}` });
        results.valid = false;
      }
    }

    if (rule.min !== undefined && typeof value === 'number' && value < rule.min) {
      results.errors.push({ field, message: `${field} must be >= ${rule.min}` });
      results.valid = false;
    }

    if (rule.max !== undefined && typeof value === 'number' && value > rule.max) {
      results.errors.push({ field, message: `${field} must be <= ${rule.max}` });
      results.valid = false;
    }

    if (rule.pattern && typeof value === 'string' && !rule.pattern.test(value)) {
      results.errors.push({ field, message: `${field} format is invalid` });
      results.valid = false;
    }
  }

  return results;
}

export default { validate };
