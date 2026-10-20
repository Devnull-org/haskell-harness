"use strict";
(() => {
  const choices = ["system", "light", "dark"];
  let selected = "system";
  try {
    const saved = localStorage.getItem("haskell-harness-theme");
    if (choices.includes(saved)) selected = saved;
  } catch {
    // The theme still works when browser storage is unavailable.
  }
  function apply(value) {
    selected = choices.includes(value) ? value : "system";
    document.documentElement.dataset.theme = selected;
    for (const picker of document.querySelectorAll("[data-theme-picker]"))
      picker.value = selected;
  }
  apply(selected);
  for (const picker of document.querySelectorAll("[data-theme-picker]"))
    picker.addEventListener("change", () => {
      apply(picker.value);
      try {
        localStorage.setItem("haskell-harness-theme", selected);
      } catch {
        // Keep the selection for this page even if it cannot be saved.
      }
    });
})();
