import { MapContainer, TileLayer, Polygon, Tooltip } from 'react-leaflet'
import { SquiggleUnderline } from './doodles.jsx'

const CARTO_KEY = process.env.REACT_APP_CARTO_KEY
const CARTO_BASE_URL = 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'
const CARTO_TILE_URL = CARTO_KEY
  ? `${CARTO_BASE_URL}?key=${encodeURIComponent(CARTO_KEY)}`
  : CARTO_BASE_URL

if (!CARTO_KEY) {
  console.warn('CARTO key missing: tiles will show watermark')
}

// Southwest / northeast corners of the county outline, with a little air.
const FORT_BEND_BOUNDS = [
  [29.2626, -96.0889],
  [29.7883, -95.4241],
]

// Fort Bend County, Texas boundary - US Census TIGER cartographic outline
// (FIPS 48157), simplified for display.
const FORT_BEND_OUTLINE = [
  [29.7109, -95.6883], [29.7107, -95.688], [29.7101, -95.6871], [29.667, -95.61],
  [29.6333, -95.5379], [29.591, -95.447], [29.5881, -95.4404], [29.5856, -95.4356],
  [29.5802, -95.4241], [29.5102, -95.4432], [29.4392, -95.4625], [29.4384, -95.55],
  [29.3401, -95.5796], [29.2909, -95.6544], [29.3245, -95.7529], [29.2626, -95.8477],
  [29.2668, -95.8796], [29.315, -95.8945], [29.3334, -95.9468], [29.3581, -95.9374],
  [29.4114, -95.9734], [29.4765, -96.0607], [29.5622, -96.0446], [29.6017, -96.0889],
  [29.6029, -96.0249], [29.7279, -96.0327], [29.7883, -95.8262], [29.7109, -95.6883],
]

export default function WhereWeAre() {
  return (
    <section id="where" className="ff-section ff-where">
      <div className="ff-container">
        <div className="ff-where__head">
          <span className="ff-eyebrow">where we work</span>
          <h2 className="ff-where__title">
            Fort Bend County, along the Brazos.
          </h2>
          <SquiggleUnderline className="ff-where__squiggle" />
          <p className="ff-where__subtitle">
            If your farm sits inside the county line (around Richmond,
            Rosenberg, Needville, Fulshear, or anywhere between),
            you&rsquo;re in our service area.
          </p>
        </div>

        <figure className="ff-where__frame">
          <div className="ff-map ff-where__map">
            <MapContainer
              bounds={FORT_BEND_BOUNDS}
              boundsOptions={{ padding: [24, 24] }}
              scrollWheelZoom={false}
              style={{ height: '100%', width: '100%' }}
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
                url={CARTO_TILE_URL}
              />
              <Polygon
                positions={FORT_BEND_OUTLINE}
                pathOptions={{
                  color: '#3e7a4e',
                  weight: 2.5,
                  dashArray: '7 6',
                  fillColor: '#5a9a68',
                  fillOpacity: 0.14,
                }}
              >
                <Tooltip sticky direction="top" offset={[0, -6]}>
                  Fort Bend County: our service area
                </Tooltip>
              </Polygon>
            </MapContainer>
          </div>
          <figcaption className="ff-where__caption">
            the actual county line. If you farm inside it, you&rsquo;re one of ours
          </figcaption>
        </figure>

        <p className="ff-where__note">
          Not sure whether you fall inside the line? Ask us below. Worst
          case, we&rsquo;ll point you somewhere good.
        </p>
      </div>
    </section>
  )
}
