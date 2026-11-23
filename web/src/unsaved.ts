/** Whether the Settings page holds changes that are not saved yet. The menu asks before leaving it, so a click on another page does not quietly throw them away. */
let settingsDirty = false;
export const unsaved = { get: () => settingsDirty, set: (v: boolean) => { settingsDirty = v; } };
