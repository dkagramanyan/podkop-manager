// state.js: part of podkop-manager's web interface (see app.js)


export const S = {
  routers: {},        // id -> snapshot from /api/routers and the stream
  schema: null,       // /api/schema
  manager: null,      // /api/manager
  view: null,         // the open page's live-update handlers
  live: false,        // the stream is connected
  work: {},           // id -> the routing draft being edited (see loadWork)
};

S.jobWatch = new Map();   // version id -> fn(job) for live job updates
S.upgradeWatch = new Map();   // router id -> fn(job) for a running agent update
