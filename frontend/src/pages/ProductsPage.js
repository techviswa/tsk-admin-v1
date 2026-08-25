import { useState, useEffect, useCallback } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
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
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', category: 'General', price: 0, stock: 0, active: true });

  const fetchProducts = useCallback(async () => {
    if (!selectedBusiness) {
      setLoading(false);
      return;
    }
    try {
      const { data } = await api.get('/products', { params: { business_id: selectedBusiness.id } });
      setProducts(data);
    } catch {
      toast.error('Failed to load products');
    } finally {
      setLoading(false);
    }
  }, [selectedBusiness]);

  useEffect(() => {
    setLoading(true);
    fetchProducts();
  }, [selectedBusiness, fetchProducts]);

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
    setForm({ name: '', category: 'General', price: 0, stock: 0, active: true });
    setSheetOpen(true);
  };

  const openEdit = (product) => {
    setEditing(product);
    setForm({
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
      if (editing) {
        await api.put(`/products/${editing.id}`, payload);
        toast.success('Product updated');
      } else {
        await api.post('/products', payload);
        toast.success('Product created');
      }
      setSheetOpen(false);
      fetchProducts();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const handleDelete = async (product) => {
    if (!window.confirm(`Delete "${product.name}"?`)) return;
    try {
      await api.delete(`/products/${product.id}`);
      toast.success('Product deleted');
      fetchProducts();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const toggleActive = async (product) => {
    try {
      await api.put(`/products/${product.id}`, { active: product.active === false });
      toast.success('Product status updated');
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
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-product-btn">
          <Plus className="h-4 w-4" /> Add Product
        </Button>
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
                  <Badge className={`text-[11px] cursor-pointer ${product.active === false ? 'bg-zinc-100 text-zinc-600 border-zinc-200' : 'bg-emerald-100 text-emerald-700 border-emerald-200'}`} onClick={() => toggleActive(product)}>
                    {product.active === false ? 'inactive' : 'active'}
                  </Badge>
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onClick={() => openEdit(product)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(product)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

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
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="product-submit-btn">{editing ? 'Update Product' : 'Create Product'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
