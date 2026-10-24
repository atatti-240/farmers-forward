WITH pt AS (
    SELECT DISTINCT mukey
    FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lon} {lat})')
),
comp AS (
    SELECT c.mukey, c.cokey, c.compname, c.comppct_r, c.compkind, c.majcompflag,
           c.drainagecl, c.hydgrp, c.taxclname,
           h.chkey, h.hzname, h.hzdept_r, h.hzdepb_r, h.om_l, h.om_r, h.om_h,
           h.sandtotal_r, h.silttotal_r, h.claytotal_r, h.cec7_r,
           CASE WHEN h.om_r IS NULL THEN 0 ELSE 1 END AS has_om
    FROM pt
    INNER JOIN component AS c ON c.mukey = pt.mukey
    OUTER APPLY (
        SELECT TOP 1 ch.chkey, ch.hzname, ch.hzdept_r, ch.hzdepb_r, ch.om_l, ch.om_r, ch.om_h,
               ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r, ch.cec7_r
        FROM chorizon AS ch
        WHERE ch.cokey = c.cokey AND (ch.desgnmaster IS NULL OR ch.desgnmaster <> 'O')
        ORDER BY CASE WHEN ch.hzdept_r IS NULL THEN 1 ELSE 0 END, ch.hzdept_r, ch.chkey
    ) AS h
),
ranked AS (
    SELECT comp.*,
           ROW_NUMBER() OVER (PARTITION BY mukey ORDER BY comppct_r DESC,
               CASE WHEN majcompflag = 'Yes' THEN 0 ELSE 1 END, has_om DESC, cokey) AS dom_rank,
           ROW_NUMBER() OVER (PARTITION BY mukey ORDER BY has_om DESC, comppct_r DESC,
               CASE WHEN majcompflag = 'Yes' THEN 0 ELSE 1 END, cokey) AS pick_rank
    FROM comp
)
SELECT TOP 1
    COUNT(pt.mukey) OVER () AS n_mukeys,
    pt.mukey, mu.musym, mu.muname, l.areasymbol, l.areaname,
    d.compname AS dom_compname, d.comppct_r AS dom_comppct_r, d.compkind AS dom_compkind,
    s.cokey, s.compname, s.comppct_r, s.compkind, s.majcompflag,
    s.drainagecl, s.hydgrp, s.taxclname,
    s.chkey, s.hzname, s.hzdept_r, s.hzdepb_r, s.om_l, s.om_r, s.om_h,
    s.sandtotal_r, s.silttotal_r, s.claytotal_r, s.cec7_r,
    tx.texture, tx.texdesc,
    CASE WHEN pt.mukey IS NULL THEN 'no_mukey'
         WHEN s.cokey IS NULL THEN 'no_components'
         WHEN s.has_om = 0 THEN 'no_surface_om'
         WHEN s.cokey = d.cokey THEN 'dominant'
         ELSE 'fallback' END AS data_status
FROM (SELECT 1 AS anchor) AS a
LEFT JOIN pt ON 1 = 1
LEFT JOIN mapunit AS mu ON mu.mukey = pt.mukey
LEFT JOIN legend AS l ON l.lkey = mu.lkey
LEFT JOIN ranked AS d ON d.mukey = pt.mukey AND d.dom_rank = 1
LEFT JOIN ranked AS s ON s.mukey = pt.mukey AND s.pick_rank = 1
OUTER APPLY (
    SELECT TOP 1 tg.texture, tg.texdesc
    FROM chtexturegrp AS tg
    WHERE tg.chkey = s.chkey AND tg.rvindicator = 'Yes'
    ORDER BY tg.chtgkey
) AS tx
ORDER BY CASE WHEN s.has_om = 1 THEN 0 ELSE 1 END, pt.mukey
