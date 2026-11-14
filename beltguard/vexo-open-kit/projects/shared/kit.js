// The kit's library, for the pages in this folder. This is the one place that knows where
// the library is. This folder is in one of two places:
//   vexo-open-kit/projects/shared      inside the kit, as it came
//   my-project/shared                  copied into your own folder, with the kit next to it
const insideKit = new URL(import.meta.url).pathname.includes('/projects/shared/');
const kit = await import(insideKit ? '../../js/src/index.js' : '../vexo-open-kit/js/src/index.js');

export const { Band, SimTransport, VexoError, codec } = kit;
