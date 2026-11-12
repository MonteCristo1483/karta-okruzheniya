import Map from "./map";
import { MapErrorBoundary } from "./map-error-boundary";

export default function Page() {
  return (
    <MapErrorBoundary>
      <Map />
    </MapErrorBoundary>
  );
}
