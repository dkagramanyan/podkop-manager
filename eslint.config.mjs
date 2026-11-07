// podkop-manager's web interface: ES modules without a build step. CI runs
// eslint on them; no-undef catches a name used without its import.
const browser = ["window", "document", "location", "history", "navigator", "fetch", "EventSource", "Node",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame", "console", "URL",
  "URLSearchParams", "Blob", "FormData", "FileReader", "localStorage", "sessionStorage", "Intl", "TextEncoder",
  "TextDecoder", "CSS", "Event", "CustomEvent", "HTMLElement", "alert", "confirm", "prompt", "crypto", "AbortController",
  "performance", "Headers", "Request", "Response", "atob", "btoa", "structuredClone", "queueMicrotask"];

export default [{
  files: ["podkop_manager/static/*.js"],
  languageOptions: { ecmaVersion: 2022, sourceType: "module",
                     globals: Object.fromEntries(browser.map((g) => [g, "readonly"])) },
  rules: { "no-undef": "error", "no-unused-vars": ["error", { args: "none", caughtErrors: "none" }],
           "no-redeclare": "error", "no-dupe-keys": "error", "no-unreachable": "error" },
}];
