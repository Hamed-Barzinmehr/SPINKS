#!/bin/bash
#PBS -N Nickel_INT2_interp_002_lam_0.1111_mult_4_quartet
#PBS -l nodes=1:ppn=12
#PBS -o Nickel_INT2_interp_002_lam_0.1111_mult_4_quartet.pbs.out
#PBS -e Nickel_INT2_interp_002_lam_0.1111_mult_4_quartet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Nickel_INT2_interp_002_lam_0.1111_mult_4_quartet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Nickel_INT2_interp_002_lam_0.1111_mult_4_quartet.inp > Nickel_INT2_interp_002_lam_0.1111_mult_4_quartet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
