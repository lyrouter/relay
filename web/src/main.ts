import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { router } from "./router";
import "./styles/app.css";
import "highlight.js/styles/github.css";

createApp(App).use(createPinia()).use(router).mount("#app");
