import { defineCustomElements } from "@coveo/atomic/loader";

defineCustomElements();

const TOKEN = "";
const ORG_ID = "";

await customElements.whenDefined("atomic-search-interface");
const el = document.querySelector("atomic-search-interface");

// Tell Atomic where to load translations (served by Vite from /public/lang/en.json)
el.languageAssetsPath = "/lang";

// Standard init
await el.initialize({
  accessToken: TOKEN,
  organizationId: ORG_ID,
});

el.executeFirstSearch();