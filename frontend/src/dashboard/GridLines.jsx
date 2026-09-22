/* ==========================================================================
   Farmers Forward - GridLines
   Draws the sampling grid's cell outlines, clipped to the field boundary, as
   one Leaflet layerGroup. Each cell is its own polygon, kept in
   `group.cellLayers` under `cellKey(row, col)` (the same key a point's `cell`
   gives), so later code can restyle a single cell, e.g. to colour it by score.
   ========================================================================== */

import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'

const PANE = 'ff-grid'
// White reads on satellite imagery; thin enough not to compete with the
// blue boundary. Not interactive, so map clicks pass straight through.
const GRID_LINE_STYLE = {
  pane: PANE,
  color: '#ffffff',
  weight: 1,
  opacity: 0.85,
  fill: false,
  interactive: false,
}

/**
 * @param {{ cells: Array<{key:string, row:number, col:number,
 *   ring:Array<[number, number]>}> }} props - Output of `gridCells(...)`;
 *   pass a memoised array so the layer is rebuilt only when the grid changes.
 */
export default function GridLines({ cells }) {
  const map = useMap()

  useEffect(() => {
    // Below the overlay pane (400) so lines never paint over sample points.
    if (!map.getPane(PANE)) map.createPane(PANE).style.zIndex = '390'

    const group = L.layerGroup()
    group.cellLayers = new Map()
    for (const cell of cells) {
      const layer = L.polygon(cell.ring, GRID_LINE_STYLE)
      layer.cell = { key: cell.key, row: cell.row, col: cell.col }
      group.cellLayers.set(cell.key, layer)
      group.addLayer(layer)
    }
    group.addTo(map)
    return () => {
      group.remove()
    }
  }, [map, cells])

  return null
}
