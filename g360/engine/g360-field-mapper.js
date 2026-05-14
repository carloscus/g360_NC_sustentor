export function mapField(sourceField, fieldMap) {
  const mapping = fieldMap[sourceField];
  if (!mapping) {
    return {
      success: false,
      targetField: null,
      message: `No mapping found for: ${sourceField}`
    };
  }
  return {
    success: true,
    targetField: mapping.target,
    transformations: mapping.transform || []
  };
}

export function validateMapping(sourceData, fieldMap) {
  const results = {
    valid: true,
    errors: [],
    warnings: []
  };

  for (const sourceField of Object.keys(sourceData)) {
    const mapping = mapField(sourceField, fieldMap);
    if (!mapping.success) {
      results.warnings.push(mapping.message);
    }
  }

  results.valid = results.errors.length === 0;
  return results;
}

export default { mapField, validateMapping };
