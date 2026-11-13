(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StepfilmStepSelection = api;
})(typeof self === "object" ? self : globalThis, function () {
  const copyStep = (step) => JSON.parse(JSON.stringify(step));
  const newId = () => `step-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;

  function moveSelected(steps, selectedIds, direction) {
    const selected = new Set(selectedIds);
    const result = [...steps];
    if (direction < 0) {
      for (let index = 1; index < result.length; index += 1) {
        if (selected.has(result[index].id) && !selected.has(result[index - 1].id)) {
          [result[index - 1], result[index]] = [result[index], result[index - 1]];
        }
      }
    } else if (direction > 0) {
      for (let index = result.length - 2; index >= 0; index -= 1) {
        if (selected.has(result[index].id) && !selected.has(result[index + 1].id)) {
          [result[index], result[index + 1]] = [result[index + 1], result[index]];
        }
      }
    }
    return result;
  }

  function duplicateSelected(steps, selectedIds, makeId = newId) {
    const selected = new Set(selectedIds);
    const indices = steps.flatMap((step, index) => selected.has(step.id) ? [index] : []);
    if (!indices.length) return { steps: [...steps], selectedIds: [] };
    const clones = indices.map((index) => ({ ...copyStep(steps[index]), id: makeId() }));
    const insertAt = indices[indices.length - 1] + 1;
    return { steps: [...steps.slice(0, insertAt), ...clones, ...steps.slice(insertAt)], selectedIds: clones.map((step) => step.id) };
  }

  function deleteSelected(steps, selectedIds) {
    const selected = new Set(selectedIds);
    const kept = steps.filter((step) => !selected.has(step.id));
    if (!kept.length) return { steps: [...steps], deletedCount: 0 };
    return { steps: kept, deletedCount: steps.length - kept.length };
  }

  return { moveSelected, duplicateSelected, deleteSelected };
});
