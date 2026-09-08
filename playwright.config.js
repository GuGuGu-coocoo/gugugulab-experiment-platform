import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'tests/browser',workers:1,timeout:60000,use:{browserName:'chromium',channel:'chrome',headless:true,launchOptions:{args:['--host-resolver-rules=MAP admin.localhost 127.0.0.1, MAP experiment.localhost 127.0.0.1']}}});
