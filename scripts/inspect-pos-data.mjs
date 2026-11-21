import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.argv[2]);
const load = file => import(pathToFileURL(path.join(root, file)).href);
const {default: env} = await load('backend/src/config/env.js');
if (!['localhost','127.0.0.1','[::1]'].includes(new URL(env.database.url).hostname)) throw Error('Local database only');
const {default: prisma} = await load('backend/src/database/prisma/client.js');
try {
  const businesses = await prisma.business.findMany({select: {id:true,name:true,tenantId:true,
    _count:{select:{products:true,orders:true,bills:true,users:true,outlets:true}}}});
  for (const b of businesses) {
    const tables = await prisma.diningTable.count({where:{businessId:b.id}});
    console.log(JSON.stringify({...b,tables}));
  }
} finally {await prisma.$disconnect();}
