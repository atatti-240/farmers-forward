/* Shared Leaflet settings for every map in the tool. */

// Patches L.Map so maps can turn; must load before any map is created.
import 'leaflet-rotate'

export const ESRI_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
export const ESRI_ATTR = 'Tiles © Esri'
// Fort Bend County, TX.
export const START_CENTER = [29.53, -95.77]
export const START_ZOOM = 12
// The field outline and unscored points: the brand's bright blue, which
// reads on satellite imagery.
export const FIELD_BLUE = '#4D8DFF'

// Every map can turn (leaflet-rotate), so the sampling grid can sit square on
// screen. Turning is driven only by the "Turn map" setting via <MapBearing>:
// no rotate button, no touch or shift-drag rotation to get lost in.
export const ROTATE_OPTIONS = {
  rotate: true,
  rotateControl: false,
  touchRotate: false,
  shiftKeyRotate: false,
  compassBearing: false,
}
