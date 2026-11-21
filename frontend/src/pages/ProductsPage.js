import { useState, useEffect, useCallback, useRef } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { displayValue } from '@/lib/posOperations';
import ProductSourceReview from '@/components/ProductSourceReview';
import { useAuth } from '@/contexts/AuthContext';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Building2, MoreHorizontal, Package, Pencil, Plus, Trash2 } from 'lucide-react';

export default function ProductsPage() {
  const { selectedBusiness } = useBusiness();
  const { user } = useAuth();
  const [review, setReview] = useState(null);
  const [result, setResult] = useState({ businessId: null, rows: [] });
  const [paging, setPaging] = useState({ businessId: null, offset: 0 });
  const offset = paging.businessId === selectedBusiness?.id ? paging.offset : 0;
  const products = result.businessId === selectedBusiness?.id && result.offset === offset ? result.rows : [];
  const total = result.businessId === selectedBusiness?.id ? result.total || 0 : 0;
  const requestVersion = useRef(0);
  const activeBusiness = useRef(selectedBusiness?.id);
  activeBusiness.current = selectedBusiness?.id;
  const activeOffset = useRef(offset);
  activeOffset.current = offset;
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', category: 'General', price: 0, stock: 0, active: true });

  const fetchProducts = useCallback(async () => {
    if (selectedBusiness?.id !== activeBusiness.current || offset !== activeOffset.current) return;
    const version = ++requestVersion.current;
    if (!selectedBusiness) {
      setLoading(false);
      return;
    }
    try {
      const { data, headers } = await api.get('/products', { params: { business_id: selectedBusiness.id, limit: 50, offset } });
      if (version === requestVersion.current) setResult({ businessId: selectedBusiness.id, offset, rows: data,
        total: Number(headers?.['x-total-count'] ?? data.length), refresh: headers?.['x-pos-refresh-status'] });
    } catch (err) {
      if (version === requestVersion.current) toast.error(`Failed to load products: ${formatApiError(err)}`);
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [selectedBusiness, offset]);

  useEffect(() => {
    setLoading(true);
    fetchProducts();
    return () => { requestVersion.current += 1; };
  }, [fetchProducts]);
  useEffect(() => { setSheetOpen(false); setEditing(null); setReview(null); }, [selectedBusiness?.id]);
  useEffect(() => {
    if (!selectedBusiness?.id) return undefined;
    const pending = result.businessId === selectedBusiness.id && (['pending', 'running', 'retrying'].includes(result.refresh) || result.rows.some(product => product.pos_sync_pending && !['failed', 'needs_reconciliation'].includes(product.pos_command_status)));
    const timer = setInterval(fetchProducts, pending ? 3000 : 30000);
    return () => clearInterval(timer);
  }, [selectedBusiness?.id, result.businessId, result.refresh, result.rows, fetchProducts]);

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="products-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business from the top navigation to manage products</p>
      </div>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', category: 'General', price: 0, stock: 0, active: true, request_id: crypto.randomUUID() });
    setSheetOpen(true);
  };

  const openEdit = (product) => {
    setEditing(product);
    setForm({
      expected_updated_at: product.pos_metadata?.updated_at || undefined,
      name: product.name || '',
      category: product.category || 'General',
      price: Number(product.price || 0),
      stock: Number(product.stock || 0),
      active: product.active !== false,
    });
    setSheetOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const payload = {
      ...form,
      business_id: selectedBusiness.id,
      price: Number(form.price || 0),
      stock: Number(form.stock || 0),
    };
    try {
      const result = editing ? await api.put(`/products/${editing.id}`, payload) : await api.post('/products', payload);
      if (payload.business_id !== activeBusiness.current) return;
      if (result.status === 202) toast.info('Product change queued. It takes effect after POS confirms it.');
      else toast.success(editing ? 'Product updated' : 'Product created');
      setSheetOpen(false);
      fetchProducts();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const handleDelete = async (product) => {
    if (!window.confirm(`Delete "${product.name}"?`)) return;
    try {
      const result = await api.delete(`/products/${product.id}`, { params: { expected_updated_at: product.pos_metadata?.updated_at } });
      if (result.status === 202) toast.info('Deletion queued. The product remains unchanged until POS confirms it.');
      else toast.success('Product deleted');
      fetchProducts();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const toggleActive = async (product) => {
    if (selectedBusiness?.pos_connection_state === 'setup_required') return;
    try {
      const result = await api.put(`/products/${product.id}`, { active: product.active === false, expected_updated_at: product.pos_metadata?.updated_at });
      if (result.status === 202) toast.info('Status change queued; waiting for POS confirmation.');
      else toast.success('Product status updated');
      fetchProducts();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  return (
    <div className="space-y-6" data-testid="products-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Products</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Catalog items for {selectedBusiness.name}</p>
        </div>
        <div className="flex flex-wrap gap-2"><Button disabled={selectedBusiness.pos_connection_state === 'setup_required'} variant="outline" onClick={async () => { try { await api.post('/pos-bridge/queue/products', null, { params: { business_id: selectedBusiness.id } }); fetchProducts(); } catch (error) { toast.error(formatApiError(error)); } }}>Refresh from POS</Button><Button disabled={selectedBusiness.pos_connection_state === 'setup_required'} onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-product-btn">
          <Plus className="h-4 w-4" /> Add Product
        </Button></div>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Name</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Category</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Price</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Stock</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={6} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : products.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center py-12 text-zinc-400">No products yet</TableCell></TableRow>
            ) : products.map(product => (
              <TableRow key={product.id} className="hover:bg-zinc-50/50">
                <TableCell className="font-medium text-zinc-900 py-3">
                  <div className="flex items-center gap-2">
                    <Package className="h-3.5 w-3.5 text-zinc-400" />{product.name}
                  </div>
                </TableCell>
                <TableCell className="text-zinc-600 py-3">{product.category || '-'}</TableCell>
                <TableCell className="text-zinc-600 py-3">{Number(product.price || 0).toFixed(2)}</TableCell>
                <TableCell className="text-zinc-600 py-3">{product.stock ?? 0}</TableCell>
                <TableCell className="py-3">
                  <Badge aria-disabled={product.pos_sync_pending || selectedBusiness.pos_connection_state === 'setup_required' || undefined} className={`text-[11px] cursor-pointer ${product.active === false ? 'bg-zinc-100 text-zinc-600 border-zinc-200' : 'bg-emerald-100 text-emerald-700 border-emerald-200'}`} onClick={() => { if (!product.pos_sync_pending) toggleActive(product); }}>
                    {product.pos_sync_pending ? (product.pos_command_status === 'needs_reconciliation' ? 'Mapping review needed' : product.pos_command_status === 'failed' ? 'POS update failed' : 'POS update pending') : product.active === false ? 'inactive' : 'active'}
                  </Badge>
                  {product.pos_sync_error && <p className="mt-1 max-w-xs text-xs text-red-600">{product.pos_sync_error.message}</p>}
                  {product.pos_command_status === 'failed' && <Button variant="outline" size="sm" onClick={async () => { try { await api.post(`/products/${product.id}/retry-pos`); toast.info('Retry queued'); fetchProducts(); } catch (error) { toast.error(formatApiError(error)); } }}>Retry POS update</Button>}
                  {product.pos_command_status === 'needs_reconciliation' && user?.role === 'platform_admin' && <Button disabled={selectedBusiness.pos_connection_state === 'setup_required'} variant="outline" size="sm" onClick={() => setReview(product)}>Review mapping</Button>}
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem disabled={product.pos_sync_pending || selectedBusiness.pos_connection_state === 'setup_required'} onClick={() => openEdit(product)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem disabled={product.pos_sync_pending || selectedBusiness.pos_connection_state === 'setup_required'} onClick={() => handleDelete(product)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between text-sm text-zinc-500"><span>{total ? `${offset + 1}–${offset + products.length} of ${total}` : '0 products'}</span><div className="flex gap-2"><Button variant="outline" disabled={loading || !offset} onClick={() => setPaging({ businessId: selectedBusiness.id, offset: Math.max(0, offset - 50) })}>Previous</Button><Button variant="outline" disabled={loading || offset + products.length >= total} onClick={() => setPaging({ businessId: selectedBusiness.id, offset: offset + 50 })}>Next</Button></div></div>
      {review && <ProductSourceReview key={review.id} product={review} onClose={() => setReview(null)} onDone={() => { if (review.business_id === activeBusiness.current) { setReview(null); fetchProducts(); toast.success('Mapping reviewed'); } }} />}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit Product' : 'New Product'}</SheetTitle></SheetHeader>
          <form onSubmit={handleSubmit} className="space-y-5 mt-6">
            <div className="space-y-2"><Label>Product Name</Label><Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} required data-testid="product-name-input" /></div>
            <div className="space-y-2"><Label>Category</Label><Input value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} data-testid="product-category-input" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label>Price</Label><Input type="number" min={0} step="0.01" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} data-testid="product-price-input" /></div>
              <div className="space-y-2"><Label>Stock</Label><Input type="number" min={0} step="1" value={form.stock} onChange={e => setForm({ ...form, stock: e.target.value })} data-testid="product-stock-input" /></div>
            </div>
            {editing?.pos_metadata && <section className="space-y-3 rounded border bg-zinc-50 p-4">
              <h3 className="font-semibold">POS menu details</h3>
              <p className="text-xs text-zinc-500">Synchronized from POS. Manage menu choices, recipes, allergens and channel settings in POS.</p>
              <dl className="space-y-3">{['description', 'dietary_type', 'image_url', 'variation_options', 'addon_options', 'modifier_groups', 'combo_components', 'removal_options', 'recipe_lines', 'channel_settings', 'outlet_overrides'].filter(key => editing.pos_metadata[key] !== undefined).map(key => <div key={key}><dt className="text-xs font-semibold uppercase text-zinc-500">{key.replace(/_/g, ' ')}</dt><dd className="mt-1 break-words text-sm">{displayValue(editing.pos_metadata[key])}</dd></div>)}</dl>
            </section>}
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="product-submit-btn">{editing ? 'Update Product' : 'Create Product'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
