declare const pdfjsLib: any;

interface Window {
  libraryAppBack?: () => void;
  libraryAppOffline?: Set<number> | { has: (id: number) => boolean };
  libraryAppIsOffline?: boolean;
  webkit?: {
    messageHandlers?: Record<string, { postMessage: (msg: any) => void }>;
  };
  pdfjsLib?: any;
}
